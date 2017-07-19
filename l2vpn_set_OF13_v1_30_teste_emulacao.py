# -*- coding: cp1252 -*-
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import HANDSHAKE_DISPATCHER, CONFIG_DISPATCHER, MAIN_DISPATCHER 
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_0, ofproto_v1_3
from ryu.ofproto import ofproto_v1_0_parser, ofproto_v1_3_parser
from ryu.topology import switches
from ryu.controller import handler, dpset
from ryu.ofproto import ether
#from ryu.lib import mac, stplib, ofctl_v1_3, hub
from ryu.lib import mac, ofctl_v1_0, ofctl_v1_3, hub
from ryu.lib.packet import ethernet, arp, packet, ipv4,icmp, tcp, udp, mpls, vlan, ether_types, llc
from ryu.lib.ip import ipv4_to_bin, ipv4_to_str
from ryu.lib.mac import haddr_to_bin
from ryu.topology import event,api
from ryu.topology.api import get_switch, get_link, get_host
from ryu.utils import hex_array
from time import time, sleep
from threading import BoundedSemaphore
import sys
import json
import logging

#1.5 - PASSAMOS A INSTALAR AS REGRAS SEM MAC_SRC, DROPANDO OS PACOTES LLC BASEADO NO MAC DE DESTINO E PRO-ATIVAMENTE EM CADA DIRECAO
#1.6 - PERMITE A LEITURA A PARTIR DO ARQUIVO DE CONFIGURACAO PELO NOME DA INTERFACE E NAO MAIS PELO ID
#1.7 - DELETA AS REGRAS QUANDO HA DELECAO DE SWITCHES OU DE LINKS
#1.8 - SUBSTITUICAO DO LOG DE DEBUG POR INFO E DOS PRINTS POR DEBUG, ADICAO DE METODO PARA ESCREVER EM UM ARQUIVO CASO OCORRA ERRO NO CALCULO DA OUT_PORT. ESSE ARQUIVO E UTILIZADO POR UM PROCESSO WATCHDOG PARA RESETAR A APLICACAO
#1.9 - ADICAO DAS VARIAVEIS GLOBAIS PARA OS TIMERS E PATHS DE ARQUIVOS.
#1.10 - CORRECAO DE PROBLEMA NA VLAN_HANDLER QUANDO VLAN_ID DE ENTRADA OU DE SAIDA E IGUAL AO VLAN_ID DE BACKBONE
#1.11 - ADICAO DE FUNCIONALIDADE DE RANGE DE VLANS
#1.12 - FUNCIONALIDADE DE QinQ (SVLAN) Obs.: O 1.11 e o 1.12 sao iguais visto que o 802.1q(vlan) e o 802.1ad(svlan) sao tratados dentro de ryu.lib.packet.vlan
#1.13 - MODIFICADO O FLOW_MOD ADICIONANDO O TABLE_ID PARA OPERAR COM O REST_QOS
#1.14 - MODIFICADO O INTERVALO DE CHECAGEM DA BASE DE IDENTIFICACAO, MODIFICADO O CALCULO DA VLAN MODIFICADA
#1.16 - ADICIONADO AO MATCH DA REGRA DE FLUXO, O ENDERECO MAC DE ORIGEM, DEVIDO AO PROBLEMA DE LOOP NOS ASR
#1.17 - ADICIONADO SEMAFOROS REFERENTES AOS OBJETOS TOPOLOGIA E A TABELA DE ROTEAMENTO
#1.18 - ADICIONADA TECNICA DE SPLI-HORIZON AO MECANISMO DE ENCAMINHAMENTO (COMO APRENDE O MAC SO ENCAMINHA NESSA INTERFACE CORRETA)
#1.19 - ADICIONADO MECANISMO DE VERIFICACAO SE AS ENTRADAS DE IDENTIFICACAO ESTAO VALIDAS, ATRAVES DE UM ARQUIVO
#1.20 - CORRIGIDO PROBLEMA NO SWITCH LEAVE HANDLER
#1.21 - CORRIGIDO PROBLEMA NO DESC STATS REPLY HANDLER
#1.22 - MODIFICADO COMENTARIOS NO VLAN HANDLER
#1.23 - MODIFICACAO NO DESC_STATS_REPLY_HANDLER PARA EVITAR ERRO
#1.24 - CORRECAO DE PROBLEMA NO SWITCH LEAVE QUANDO OCORRE GERACAO DE EVENTO INDEVIDO
#1.26 - INICIO SUPORTE OPENFLOW 1.0
#1.27 - ADICAO SWITCH FEATURES CONFIG DISPATCHER PARA EVITAR ERRO DE DESCONEXAO
#1.28 - MODIFICACAO TOTAL DA FORMA DE PLOT DOS LOGS (LogRecord attributes)
#1.29 - CORRECAO DE ERRO NO VLAN HANDLER QUANDO OS SWITCHES NA TOPOLOGIA NAO ESTAO INTERCONECTADOS
#1.30 - MODIFICACAO DO TRATAMENTO DOS PACOTES VLAN ONDE PARA CONTINUAR PROCESSAMENTO DE UMA ENTRADA CONSIDERA TAMBEM O VLAN_ID (PARA O CASO DE MULTIPLOS CIRCUITOS NA MESMA INTERFACE)

MAC_SRC_RENEWAL_INTERVAL = 60 #in seconds
CONFIGURATION_DB = '/home/mininet/SDN/Projeto_L2VPN_RR/l2vpn.conf'
IDENTIFICATION_DB = '/home/mininet/SDN/Projeto_L2VPN_RR/l2vpn_ids.conf'
CONFIGURATION_DB_VERIFICATION_INTERVAL = 5 #in_seconds
DESC_REQUEST_INTERVAL = 100 #in seconds
INTERFACES_INFO_FILE = '/tmp/interfaces_info.json'
INTERFACES_INFO_OO_FILE = '/tmp/interfaces_info_object.json'
LLDP_TAGGED_VERIFICATION_FILE = '/tmp/verification_lldp_discovery.txt'
IDENTIFICATION_DB_VERIFICATION_FILE = '/tmp/identification_db_verification.txt'
FIRST_LINK_ADD_INTERVAL = 40 #in seconds
FIRST_BACKBONE_ID = 302 #first VLAN ID to be used as backbone id
ECHO_REQUEST_INTERVAL = 1
#TIMEOUT_ECHO_REQUEST = 3 * ECHO_REQUEST_INTERVAL
TIMEOUT_ECHO_REQUEST = 60.0

class portas:
    def __init__(self, int_saida_src, int_entrada_dst, custo=1):
        self.int_saida_src = int_saida_src #interface de saida do switch fonte
        self.int_entrada_dst = int_entrada_dst #interface de entrada do switch destino
        self.custo = custo # custo do enlace

    def __repr__(self):
        return "[" + str(self.int_saida_src) + "," + str(self.int_entrada_dst) + "," + str(self.custo) + "]"

class dados_dijkstra:
    def __init__(self, precedente, estimativa, visitado):
        self.precedente = precedente # switch destino, id do switch
        self.estimativa = estimativa # custo para o switch destino, inteiro para o switch destino
        self.visitado = visitado # foi calculado para o path para o caminho destino, sim ou nao

    def __repr__(self):
        return str(self.precedente) + "," + str(self.estimativa) + "," + str(self.visitado)

class saida:
    def __init__(self, switch, porta_saida, num_salto):
        self.switch = switch
        self.porta_saida = porta_saida
        self.num_salto = num_salto

    def __repr__(self):
        return "salto: " + str(self.num_salto) + "," + " switch: " + str(self.switch) + "," + " porta_saida: " + str(self.porta_saida) + ";"

class calculo_dijkstra: # Retorna um objeto com a tabela de roteamento
    def __init__(self, topologia):
        self.tabela_dijkstra = {}
        self.tabela_roteamento_completa = {}
        verificador = []
        prosseguimento = "sim"
        self.logger = logging.getLogger('spf_calculation_application')
        if not len(self.logger.handlers):
            self._set_logger()

        self.logger.info('Routing table calculation has been initiated.')
        self.logger.info('Topology object is: %s.', str(topologia))

        """Calcula a tabela dijkstra para todos os switches da topologia"""
        """Para realizar o calculo tem que verificar se para cada switch dest de cada switch origem existe um switch origem (verifica se existe o par switch_origem:switch_destino e switch_destino_switch_origem)"""
        for switch_origem in topologia.keys():
            for switch_destino in topologia[switch_origem]:
                if topologia.has_key(switch_destino):
                    if topologia[switch_destino].has_key(switch_origem):
                        verificador.append("ok")
                    else:
                        verificador.append("nok")
                else:
                    verificador.append("nok")

        for i in verificador:
            if (i == "nok"):
                self.logger.info('Proceeding could not be completed due to topology mismatch.')
                prosseguimento = "nao"

        if (prosseguimento == "sim"):
            for switch in topologia.keys():
                self.tabela_dijkstra[switch] = self.calcula(self.inicializacao(topologia), topologia, switch)

            self.tabela_roteamento_completa = self.monta_tabela_roteamento(self.tabela_dijkstra, topologia)

    def _set_logger(self):
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        hdlr = logging.StreamHandler()
        fmt_str = '[SPF][%(levelname)s] %(funcName)s | %(created)f | %(asctime)s: %(message)s'
        hdlr.setFormatter(logging.Formatter(fmt_str))
        self.logger.addHandler(hdlr)

    def __repr__(self): # Metodo que retorna a maneira como o objeto da classe e printado na tela
        return  str(self.tabela_roteamento_completa)

    def inicializacao(self, topologia): # faz todos os vertices
        # Cria uma um dicionario em que as chaves sao topologia.keys() e os valores sao o objeto dados_dijkstra
        # Essa tabela e utilizada para o calculo do algoritmo Dijkstra (uma tabela por topologia)
        tabela_dijkstra = {}
        for switch in topologia.keys():
            tabela_dijkstra[switch] = dados_dijkstra(None, float("inf"), "nao")

        self.logger.debug('Routing table has been initiated. %s', tabela_dijkstra)
        return tabela_dijkstra

    def calcula(self, tabela_inicializada, topologia, switch_raiz):
        #Algoritmo para calculo da tabela dijkstra
        """Atribua valor zero a estimativa do custo minimo do vertice s (raiz da busca) e infinito as demais estimativas)"""
        """Marque a raiz como precedente da propria raiz"""
        tabela_inicializada[switch_raiz].estimativa = 0
        tabela_inicializada[switch_raiz].precedente = switch_raiz

        for switch_vizinho_raiz in topologia[switch_raiz].keys():
            tabela_inicializada[switch_vizinho_raiz].estimativa = topologia[switch_raiz][switch_vizinho_raiz].custo # ******* atribui o custo
            tabela_inicializada[switch_vizinho_raiz].precedente = switch_raiz

        """Marque a raiz como visitada"""
        tabela_inicializada[switch_raiz].visitado = "sim"

        switches_nao_visitados = 1
        """Enquanto existirem vertices (switches) nao visitados"""
        while switches_nao_visitados == 1:
            """Escolha um vertice k ainda nao visitado cuja estimativa seja a menor dentre todos os vertices nao visitados"""
            #Cria um dicionatio com as estimativas do switches vizinhos nao visitados
            estimativas = {}
            for switch in tabela_inicializada.keys():
                if (tabela_inicializada[switch].visitado == "nao"):
                    estimativas[switch] = tabela_inicializada[switch].estimativa
            # Referencia: stackoverflow get key with the least value from a dictionary
            switch_vizinho_menor_estimativa = min(estimativas, key=estimativas.get) #retorna o switch com menor estimativa

            """Para todos os vizinhos de k"""
            for switches_vizinhos_switch_menor_estimativa in topologia[switch_vizinho_menor_estimativa].keys():
                """Some a estimativa do vertice k com o custo do arco que une k (switch_vizinho_menor_estimativa) a j(switches_vizinhos_switch_menor_estimativa)"""
                nova_estimativa = tabela_inicializada[switch_vizinho_menor_estimativa].estimativa + topologia[switches_vizinhos_switch_menor_estimativa][switch_vizinho_menor_estimativa].custo
                if ( nova_estimativa < tabela_inicializada[switches_vizinhos_switch_menor_estimativa].estimativa ):
                    tabela_inicializada[switches_vizinhos_switch_menor_estimativa].estimativa = nova_estimativa
                    tabela_inicializada[switches_vizinhos_switch_menor_estimativa].precedente = switch_vizinho_menor_estimativa

            """Marque k (switch_vizinho_menor_estimativa) como visitado"""
            tabela_inicializada[switch_vizinho_menor_estimativa].visitado = "sim"

            """Enquanto existirem vertices (switches) nao visitados"""
            switches_nao_visitados = 0
            for switch in tabela_inicializada.keys():
                if (tabela_inicializada[switch].visitado == "nao"):
                    switches_nao_visitados = 1

        return tabela_inicializada

    def monta_tabela_roteamento(self, tabela_todos_switches, topologia):
        #Exemplo do recebimento {1: {1: {'visitado': 'sim', 'precedente': 1, 'estimativa': 0}, 2: {'visitado': 'sim', 'precedente': 1, 'estimativa': 1}, 3: {'visitado': 'nao', 'precedente': 2, 'estimativa': 2}, 4: {'visitado': 'sim', 'precedente': 1, 'estimativa': 1}, 5: {'visitado': 'sim', 'precedente': 1, 'estimativa': 1}}
        #tabela_roteamento = {switch_origem:{switch_destino:[switch, porta_saida; switch, porta_saida; ultimo_switch, None]}}
        tabela_roteamento = {}

        # Faz verificacao se a topologia esta dividida em duas ou mais partes. Se estiver retorna a tabela de roteamento vazia
        for sw_ori in tabela_todos_switches.keys():
            for sw_dest in tabela_todos_switches[sw_ori].keys():
                if ( tabela_todos_switches[sw_ori][sw_dest].precedente == None ): # Se o precedente de qualquer switch na tabela_dijkstra for None (ou seja, ele nao entrou no calculo de vizinhos, entao a topologia esta desmenbrada) entao nao continua
                    return tabela_roteamento

        for switch_origem in tabela_todos_switches.keys():
            tabela_roteamento[switch_origem] = {}
            for switch_destino in tabela_todos_switches[switch_origem].keys():
                if ( switch_destino != switch_origem ):
                    #print "A topologia antes do encontra caminho e: ", topologia
                    #print "Antes do encontra caminho o switch origem e: ", switch_origem, "e o switch destino e: ", switch_destino
                    tabela_roteamento[switch_origem][switch_destino] = self.encontra_caminho(switch_origem, switch_destino, tabela_todos_switches, topologia)

        return tabela_roteamento

    def encontra_caminho(self, sw_origem, sw_destino, tabela_todos_switches, topologia):
        """Encontra caminho entre um switch de origem e um destino atraves da topologia"""
        #Exemplo do recebimento tabela_todos_switches {1: {1: {'visitado': 'sim', 'precedente': 1, 'estimativa': 0}, 2: {'visitado': 'sim', 'precedente': 1, 'estimativa': 1}, 3: {'visitado': 'nao', 'precedente': 2, 'estimativa': 2}, 4: {'visitado': 'sim', 'precedente': 1, 'estimativa': 1}, 5: {'visitado': 'sim', 'precedente': 1, 'estimativa': 1}}

        caminho_switches = [] #Lista com o dpid dos switches com o caminho de tras para frente, do sw_destino para o sw_origem
        salto_anterior = sw_destino
        while ( salto_anterior != sw_origem ):
            caminho_switches.append(salto_anterior)
            salto_anterior = tabela_todos_switches[sw_origem][salto_anterior].precedente

        caminho_switches.append(salto_anterior) # Faz o append do switch origem    
        caminho_switches = caminho_switches[::-1] # Inverte a lista com o caminho para obter o caminho na ordem de sw_origem a sw_destino

        caminho = []
        contador_saltos = 0
        for posicao_switch_saida in range(len(caminho_switches)):
            if ( caminho_switches[posicao_switch_saida] != sw_destino ):
                interface_saida = topologia[caminho_switches[posicao_switch_saida]][caminho_switches[posicao_switch_saida+1]].int_saida_src

            else:
                interface_saida = None

            caminho.append(saida(caminho_switches[posicao_switch_saida], interface_saida, contador_saltos))
            contador_saltos = contador_saltos + 1

        return caminho

class L2VPN(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_0.OFP_VERSION,  ofproto_v1_3.OFP_VERSION]    

    _CONTEXTS = {
         'dpset': dpset.DPSet,
         #'stplib': stplib.Stp,
         } # Especifica os contextos que a classe quer utilizar

    def __init__(self, *args, **kwargs):
        super(L2VPN, self).__init__(*args, **kwargs)
        self.dpset = kwargs['dpset']
        self.obj_topologia={} # Dicionario para cada enlace (unidirecional), em que cada entrada tem o formato switch_src:{switch_dest:[port_src,port_dest]} ex. {1:{2: [2, 2]}, 2: {3: [3, 2]}, 3: {2: [2, 3]}}
        self.s_obj_topologia = BoundedSemaphore()
        self.obj_topologia_auxiliar=[] # Lista onde cada posicao e um dicionario: {switch_src:{switch_dest:[port_src,port_dest, custo]}} ex. {1:{2: [2, 2, 1]}, 2: {3: [3, 2, 1]}, 3: {2: [2, 3, 1]}}. E utilizado no caso de multiplos links, entre dois switches iguais, para manter historico
        self.s_obj_topologia_auxiliar = BoundedSemaphore()
        self.tabela_roteamento = None
        self.s_tabela_roteamento = BoundedSemaphore()
        self.mac_to_port = {} #ex.: {1: {'00:00:00:00:00:01': {'porta': 1, 'vlan_id': 10}, 'ce:cb:7d:05:9b:0d': {'porta': 2, 'vlan_id': 20}, 2: {'00:00:00:00:00:02': {'porta': 1, 'vlan_id': 20}, 'b6:fb:71:61:73:82': {'porta': 2, 'vlan_id': 30}}}
        self.timestamp_novo_mac_src = {} # Dicionario com timestamps por MAC
        self.timestamp_antigo_mac_src = {} # Dicionario com timestamps por MAC
        self.intervalo_renovacao_mac_src = MAC_SRC_RENEWAL_INTERVAL

        self.topology_api_app = self
        self.switch_list = []
        self.switches_in_topology = {}
        self.switches_in_topology_file = {}
        self.dpid_match_actions_dic = {}
        self.dictionary_dpid_datapath = {} # Dicionario do tipo {DPID:datapath} para poder instalar regras proativamente em cada direcao do circuito

        self.arquivo_configuracao_l2vpn = CONFIGURATION_DB
        self.arquivo_identificacao_l2vpn = IDENTIFICATION_DB
        self.dicionario_l2vpn = [] #Cada posicao da lista e uma linha do arquivo de configuracao que e um dicionario
        self.dicionario_l2vpn_identificacao = [] #Cada posicao da lista e um dicionario com a identificacao do circuito (PE 1, ID PE 1, PE 2, ID PE 2, ID Backbone). As informacoes do circuito sao obtidas a partir do arquivo de configuracao e os IDs de Backbone sao setados em sequencia a partir do que e lido no arquivo de configuracao
        self.frequencia_verificacao_arquivo_configuracao = CONFIGURATION_DB_VERIFICATION_INTERVAL # Tempo em segundos que a funcao verifica se ha nova configuracao no arquivo de configuracao
        self.time_to_desc_request = DESC_REQUEST_INTERVAL #in seconds
        self.desc_request_counter = 0
        self.interfaces_info = INTERFACES_INFO_FILE
        self.interfaces_info_object = INTERFACES_INFO_OO_FILE
        self.verification_lldp_discovery_file = LLDP_TAGGED_VERIFICATION_FILE

        self.last_link_add = 0
        self.begging_time = time()

	self.logger = logging.getLogger('spf_calculation_application')
        if not len(self.logger.handlers):
            self._set_logger()

        self.thread_lldp_discovery_check = hub.spawn(self.lldp_discovery_check)
        self.thread_desc_stats = hub.spawn(self.send_desc_stats_request)
        self.thread_inicializacao = hub.spawn(self.inicializacao)
        self.thread_echo_request = hub.spawn(self.echo_request)

    def _set_logger(self):
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        hdlr = logging.StreamHandler()
        fmt_str = '[VCFlow][%(levelname)s] %(funcName)s | %(created)f | %(asctime)s: %(message)s'
        hdlr.setFormatter(logging.Formatter(fmt_str))
        self.logger.addHandler(hdlr)

    def inicializacao(self):
        hub.spawn(self.verificacao_arquivo_configuracao(self.arquivo_configuracao_l2vpn, self.arquivo_identificacao_l2vpn, self.frequencia_verificacao_arquivo_configuracao))

    def echo_request(self):
        while True:
            self.logger.debug('Sending ECHO REQUESTs for DPIDs: %s.', str(self.dictionary_dpid_datapath))
            for dpid in self.dictionary_dpid_datapath.keys():
                self.logger.debug('Sending ECHO REQUEST for DPID: %s.', str(dpid))
                datapath = self.dictionary_dpid_datapath[dpid]
                self.send_echo_request(datapath)
        
            hub.sleep(ECHO_REQUEST_INTERVAL)
        

    def verificacao_arquivo_configuracao(self, arquivo_configuracao, arquivo_identificacao, frequencia_verificacao_l2vpn=300, arquivo_watchdog_databases=IDENTIFICATION_DB_VERIFICATION_FILE):
        #sleep(2) # Sleep para aguardar conexao do switches ao controlador
        primeira_leitura = True
        dicionario_l2vpn_inicio_verificacao = self.dicionario_l2vpn
        while True:
            if primeira_leitura:
                sleep(self.frequencia_verificacao_arquivo_configuracao) # Sleep para aguardar conexao do switches ao controlador
                primeira_leitura = False
                ids_backbone = [FIRST_BACKBONE_ID - 1]

            else:
                arquivo = open(arquivo_identificacao, 'r')
                linhas = arquivo.readlines()
                arquivo.close()
                ids_backbone = []
                dicionario_l2vpn_identificacao = {}
                for linha in linhas:
                    linha = linha.split(' ')
                    if '#' in linha[0]:
                        continue
                    else:
                        ids_backbone.append(int(linha[-1]))

                if ids_backbone == []: #Quando na ultima verificacao nao houve nenhuma entrada valida
                    ids_backbone = [FIRST_BACKBONE_ID - 1] # Primeira ID de backbone e 2

            arquivo = open(arquivo_configuracao, 'r')
            #self.dicionario_l2vpn = []
            #self.dicionario_l2vpn_identificacao = []
            linhas = arquivo.readlines()
            arquivo.close()
            for linha in linhas:
                dicionario_l2vpn_verificacao ={}
                dicionario_l2vpn_identificacao = {}
                linha = linha.split(' ')
                #break_reading = None

                if '#' in linha[0]:
                    continue

                elif (len(linha) == 6):
                    break_reading = False
                    for posicao in range(len(linha)):
                        try:
                            if posicao == 0:
                                dicionario_l2vpn_verificacao['switch_entrada'] = int(linha[posicao])
                                dicionario_l2vpn_identificacao['pe_1'] = int(linha[posicao])
                            elif posicao == 1:
                                dicionario_l2vpn_verificacao['porta_entrada'] = self.interface_name_reading(dicionario_l2vpn_verificacao['switch_entrada'], linha[posicao])
                                if dicionario_l2vpn_verificacao['porta_entrada'] == 0:
                                    break_reading = True
                                    break
                            elif posicao == 2:
                                try:
                                    dicionario_l2vpn_verificacao['vlan_id_entrada'] = int(linha[posicao])
                                    dicionario_l2vpn_identificacao['id_pe_1'] = int(linha[posicao])
                                    #dicionario_l2vpn_verificacao['vlan_id_entrada_range'] = False
                                    #dicionario_l2vpn_identificacao['id_pe_1_range'] = False
                                except ValueError: # Caso de range de VLANs em que a leitura e uma string
                                    #dicionario_l2vpn_verificacao['vlan_id_entrada_range'] = True
                                    #dicionario_l2vpn_identificacao['id_pe_1_range'] = True
                                    dicionario_l2vpn_verificacao['vlan_id_entrada'] = range(int(linha[posicao].split('-')[0]), (int(linha[posicao].split('-')[1])+1))
                                    dicionario_l2vpn_identificacao['id_pe_1'] = range(int(linha[posicao].split('-')[0]), (int(linha[posicao].split('-')[1])+1))
                            elif posicao == 3:
                                dicionario_l2vpn_verificacao['switch_saida'] = int(linha[posicao])
                                dicionario_l2vpn_identificacao['pe_2'] = int(linha[posicao])
                            elif posicao == 4:
                                dicionario_l2vpn_verificacao['porta_saida'] = self.interface_name_reading(dicionario_l2vpn_verificacao['switch_saida'], linha[posicao])
                                if dicionario_l2vpn_verificacao['porta_saida'] == 0:
                                    break_reading = True
                                    break
                            elif posicao == 5:
                                try:
                                    dicionario_l2vpn_verificacao['vlan_id_saida'] = int(linha[posicao].replace('\n', ''))
                                    dicionario_l2vpn_identificacao['id_pe_2'] = int(linha[posicao].replace('\n', ''))
                                    #dicionario_l2vpn_verificacao['vlan_id_saida_range'] = False
                                    #dicionario_l2vpn_identificacao['id_pe_2_range'] = False
                                except ValueError: # Caso de range de VLANs em que a leitura e uma string
                                    #dicionario_l2vpn_verificacao['vlan_id_saida_range'] = True
                                    #dicionario_l2vpn_identificacao['id_pe_2_range'] = True
                                    dicionario_l2vpn_verificacao['vlan_id_saida'] = range(int(linha[posicao].replace('\n','').split('-')[0]), (int(linha[posicao].replace('\n','').split('-')[1])+1))
                                    dicionario_l2vpn_identificacao['id_pe_2'] = range(int(linha[posicao].split('-')[0]), (int(linha[posicao].split('-')[1])+1))

                        except ValueError:
                            self.logger.info('Value error, please verify the configuration file: %s', str(arquivo_configuracao))

                if break_reading:
                    self.logger.info('Port names configured wrongly for circuit entry %s. Please verify the configuration file: %s', str(linha), str(arquivo_configuracao))
                    continue

                if ( dicionario_l2vpn_verificacao in self.dicionario_l2vpn ) or ( dicionario_l2vpn_verificacao == {} ):
                    continue
                else:
                    self.dicionario_l2vpn.append(dicionario_l2vpn_verificacao)
                    next_id_backbone = max(ids_backbone) + 1
                    ids_backbone.append(next_id_backbone)
                    dicionario_l2vpn_identificacao['id_backbone'] = next_id_backbone
                    self.dicionario_l2vpn_identificacao.append(dicionario_l2vpn_identificacao)

            if self.dicionario_l2vpn == [] :
                self.logger.info('No valid input set in the configuration file: %s', str(arquivo_configuracao))
                arquivo = open(arquivo_watchdog_databases, 'w')
                arquivo.write('0')
                arquivo.close()
            else:
                arquivo = open(arquivo_watchdog_databases, 'w')
                arquivo.write('1')
                arquivo.close()

            arquivo = open(arquivo_identificacao, 'w')
            arquivo.write('#Formato: PE 1 | ID PE 1 | PE 2 | ID PE 2 | ID Backbone\n')
            for circuito in self.dicionario_l2vpn_identificacao:
                arquivo.write(str(circuito['pe_1']) + " " + str(circuito['id_pe_1']) + " " + str(circuito['pe_2']) + " " + str(circuito['id_pe_2']) + " " + str(circuito['id_backbone']) + '\n')

            arquivo.close()

            if str(self.dicionario_l2vpn) != str(dicionario_l2vpn_inicio_verificacao):
                self.logger.info('Configuration Database has been UPDATED.')
            else:
                self.logger.info('Configuration Database has NOT been UPDATED.')

            self.logger.info('Configuration file %s has the following valid inputs: %s.', str(arquivo_configuracao), str(self.dicionario_l2vpn))
            self.logger.info('Identification file %s has the following valid inputs: %s', str(arquivo_identificacao), str(self.dicionario_l2vpn_identificacao))
            self.logger.info('Sleeping %s seconds for next configuration file verification.', str(frequencia_verificacao_l2vpn))

            hub.sleep(frequencia_verificacao_l2vpn)
        return


    def interface_name_reading(self, dpid, interface_name):
        self.logger.debug('Begging of interface name reading.')
	#print "O switches in topology e: ", self.switches_in_topology
        port_no = 0 # Port number que nao e utilizado nos switches, entado pode ser utilizado como caso de erro 
	#REMOVER PARA EMULACAO
        """
        interface_type = interface_name.split('0')[0]
        self.logger.debug('Interface type is [Gigabit|TenGigabit]: %s.', str(interface_type))

        if interface_type == 'g' or interface_type == 'G' or interface_type == 'gi' or interface_type == 'gig' or interface_type == 'Gig':
            interface_name = interface_name.replace(interface_type, 'Gi')
            self.logger.debug('Interface name modified to: %s.', str(interface_name))

        elif interface_type == 't' or interface_type == 'T' or interface_type == 'te' or interface_type == 'ten' or interface_type == 'Ten':
            interface_name = interface_name.replace(interface_type, 'Te')
            self.logger.debug('Interface name modified to: %s.', str(interface_name))

        elif interface_type != 'Gi' and interface_type != 'Te':
            # Caso em que esta configurado errado
            self.logger.debug('Interface type is different of Gi and Te. Port no is %s. Returning.', str(port_no))
            return port_no
        """
        #FIM DA REMOCAO PARA EMULACAO	

        self.logger.debug('Dictionary self.switches_in_topology is: %s.', str(self.switches_in_topology))
        name_read = False
        if self.switches_in_topology.has_key(dpid):
            self.logger.debug('Dictionary self.switches_in_topology has DPID: %s.', str(dpid))
            for i in range(len(self.switches_in_topology[dpid])):
                self.logger.debug('Interface evaluated from dictionary self.switches_in_topology is: %s.', str(self.switches_in_topology[dpid][i]))
                try:
                    if interface_name == self.switches_in_topology[dpid][i]['name']:
                        port_no = int(self.switches_in_topology[dpid][i]['port_no'])
                        name_read = True
                        self.logger.debug('Interface name is: %s is equal to switches_in_topology[dpid][i][name]: %s, port no is: %s.', str(interface_name), str(self.switches_in_topology[dpid][i]['name']), port_no)

                except Exception:
                    pass

        if not name_read: #Se o nome nao for lido o name_read e False, logo not name_read e True
            self.logger.info('Interface name: %s is not registered on DPID: %s.', str(interface_name), str(dpid))

        try:
            self.logger.debug('Returning port_no: %s.', str(port_no))
            return port_no
        except Exception:
            return

        return


    def send_desc_stats_request(self):
        while True:
            if self.desc_request_counter == 0:
                self.logger.info('#1 L2VPN Ryu App: initiated.')
                self.logger.info('Sleeping 15 seconds waiting for switches to connect.')
                #Aguarda os 15 segundos iniciais para os switches poderem se conectar ao controlador
                hub.sleep(15)

            else:
                for datapath in self.dpset.dps.values():
                    ofp_parser = datapath.ofproto_parser

                    req = ofp_parser.OFPDescStatsRequest(datapath, 0)
                    datapath.send_msg(req)
                    self.logger.info('OFPDescStatsRequest sent to DPID %s.', str(datapath.id))

                    self.logger.info('Sleeping %s seconds for next OFPDescStatsRequest.', str(self.time_to_desc_request))
                hub.sleep(self.time_to_desc_request)

            self.desc_request_counter = self.desc_request_counter + 1

        return

    def lldp_discovery_check(self):
        sleep(FIRST_LINK_ADD_INTERVAL) #Aguarda o tempo para que possa ocorrer o primeiro link_add
        interval_between_begging_and_first_link_add = self.last_link_add - self.begging_time

        if interval_between_begging_and_first_link_add < 0: # Caso que nao ocorre link add
            self.lldp_discovery_verification(False)
            self.logger.info('LLDP discovery is NOT working.')
        else:
            self.lldp_discovery_verification(True)
            self.logger.info('LLDP discovery is working.')

        return


    def lldp_discovery_verification(self, behavior):
        # Escreve em um arquivo que e utilizado como watchdog do processo Ryu. Caso o arquivo for 1 o funcionamento do LLDP tagged esta OK, caso seja 0 o funcionamento nao esta OK
        arquivo = open(self.verification_lldp_discovery_file, 'w')
        if behavior:
            arquivo.write('1')
        else:
            arquivo.write('0')

        arquivo.close()

        return


    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

	try:
            #Switches OpenFlow 1.3
            inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                                 actions)]
        except AttributeError:
            #Switches OpenFlow 1.0
            pass

        if buffer_id:
            try:
                #Switches OpenFlow 1.3
                mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                        priority=priority, match=match,
                                        instructions=inst, idle_timeout=60, hard_timeout=0)
                self.logger.info('Adding flow for OF1.3, OFPFlowMod: %s.', str(mod))
            except UnboundLocalError:
                #Switches OpenFlow 1.0
                mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                        priority=priority, match=match,
                                        actions=actions, idle_timeout=60, hard_timeout=0)
                self.logger.info('Adding flow for OF1.0, OFPFlowMod: %s.', str(mod))
                
        else:
            try:
                #Switches OpenFlow 1.3
                mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                        match=match, instructions=inst, idle_timeout=60, hard_timeout=0)
                self.logger.info('Adding flow for OF1.3, OFPFlowMod: %s.', str(mod))
            except UnboundLocalError:
                #Switches OpenFlow 1.0
                mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                        match=match, actions=actions, idle_timeout=60, hard_timeout=0)
                self.logger.info('Adding flow for OF1.0, OFPFlowMod: %s.', str(mod))
        datapath.send_msg(mod)
        self.logger.info('Flow added for DPID: %s ', str(datapath.id))
        self.logger.info('Flow added with match: %s and actions %s', str(match), str(actions))

        new_dic_match_actions = {'match': match, 'actions': actions}
        continuacao = True
        if self.dpid_match_actions_dic.has_key(datapath.id):
            for dic_match_actions_comparison in self.dpid_match_actions_dic[datapath.id]:
                if str(dic_match_actions_comparison) == str(new_dic_match_actions):
                    continuacao = False

            if continuacao:
                self.dpid_match_actions_dic[datapath.id].append(new_dic_match_actions)
                self.logger.info('Match and Actions Dictionary for DPID: %s has been UPDATED: %s', str(datapath.id), str(self.dpid_match_actions_dic))

        else:
            self.dpid_match_actions_dic[datapath.id] = []
            self.dpid_match_actions_dic[datapath.id].append(new_dic_match_actions)
            self.logger.info('Match and Actions Dictionary for DPID: %s has been CREATED: %s', str(datapath.id), str(self.dpid_match_actions_dic))

        return


    def mod_flow(self, datapath, priority, match, actions, command, cookie=1, out_port=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        try:
            # Switches OpenFlow 1.3
            mod = parser.OFPFlowMod(datapath=datapath, match=match, cookie=cookie, command=command, out_port=ofproto.OFPP_ANY, out_group=ofproto.OFPG_ANY, priority=priority)
        except AttributeError:
            # Switches OpenFlow 1.0
            mod = parser.OFPFlowMod(datapath=datapath, match=match, cookie=cookie, command=command, actions=actions, priority=priority)

        datapath.send_msg(mod)
        self.logger.info('Flow deleted for DPID: %s with match: %s and actions %s', str(datapath.id), str(match), str(actions))

        return


    def packet_out(self, datapath, msg, in_port, actions):
        #O PACKET-OUT nao modifica a tag de VLAN entao retiramos o PACKET-OUT
        #Nao da para fazer assim pois dependendo do numero de hops demorasse muito ate fechar o circuito
        ofp = datapath.ofproto
        ofp_parser = datapath.ofproto_parser

        data = None
        if (msg.buffer_id == ofp.OFP_NO_BUFFER):
            data = msg.data

        """
        #actions2 = [ofp_parser.OFPActionOutput(out_port),ofp_parser.OFPActionSetField(vlan_vid=vlan_vid_modified)]
        actions2 = [ofp_parser.OFPActionOutput(ofp.OFPP_TABLE)]
        try:
            self.logger.debug('Actions 2 is: %s', str(actions2))
        except Exception:
            pass
        """

        out = ofp_parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port, actions=actions, data=data)
        #out = ofp_parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port, actions=actions2, data=data)
        datapath.send_msg(out)
        self.logger.info('Packet-out sent to DPID: %s in_port: %s actions: %s', str(datapath.id), str(in_port), str(actions))

        return

    def llc_drop_handler(self, datapath, msg, in_port, mac_dst):
        # Dropa os pacotes LLC para nao ocorrer problemas quando os switches CE conectados aos endpoints tem VLAN ID diferentes e utilizam STP
        ofp = datapath.ofproto
        ofp_parser = datapath.ofproto_parser
        dpid = datapath.id
        match = ofp_parser.OFPMatch(in_port=in_port, eth_dst=mac_dst) #O Mac_dst e especifico de pacotes LLC
        actions = []

        self.add_flow(datapath, 2, match, actions)

        return


    def vlan_handler(self, datapath, vlan_id, in_port, mac_src, vlan_vid_modified_last_switch=0):
        ofp = datapath.ofproto
        ofp_parser = datapath.ofproto_parser
        dpid = datapath.id
        modificacao_vlan_vid = False
        instalacao_proativa = False

        #INICIO DO NOVO TRATAMENTO
        self.lldp_discovery_verification(True)# Inicializa o arquivo de verificacao de funcionamento do LLDP tagged discovery em True (funcionando). E modificado caso ocorra erro no calculo do out_port.
        switch_origem = dpid
	#print "O switch origem e: ", switch_origem
        switch_destino = 0
        break_circuit_ident_loop = False
        #out_port_found = False #Utilizado para garantir que o out_port e a modificacao de vlan ID nao sejam sobrescritos
        for circuito_conf in self.dicionario_l2vpn:
            for circuito_ident in self.dicionario_l2vpn_identificacao:
                self.logger.info('Circuit from configuration database (circuit_conf) is: %s. Circuit from identification database (circuit_ident) is: %s.', str(circuito_conf), str(circuito_ident))
                if (circuito_conf['switch_entrada'] == circuito_ident['pe_1']) and (circuito_conf['switch_saida'] == circuito_ident['pe_2']) and (circuito_conf['vlan_id_entrada'] == circuito_ident['id_pe_1']) and (circuito_conf['vlan_id_saida'] == circuito_ident['id_pe_2']):
                    self.logger.debug('circuit_conf == circuit_ident. Initiating handling...')
                    self.logger.debug('mac_to_port table is: %s.', str(self.mac_to_port))
                    for switch in self.mac_to_port.keys():
                        self.logger.debug('Switch from mac_to_port table is: %s.', str(switch))
                        if self.mac_to_port[switch].has_key(mac_src):
                            self.logger.debug('mac_to_port table for switch: %s has the MAC source: %s.', str(switch), str(mac_src))
                            if self.mac_to_port[switch][mac_src]['porta'] == circuito_conf['porta_entrada'] and switch == circuito_conf['switch_entrada'] and self.mac_to_port[switch][mac_src]['vlan_id'] == circuito_conf['vlan_id_entrada']:
                                #Identifica a direcao 1 => 2
                                self.logger.debug('Direction (1) => (2) identified.')
                                switch_destino = circuito_conf['switch_saida']
                                self.logger.debug('Destination switch is: %s.', str(switch_destino))
                                direcao = '1_to_2'

                            elif self.mac_to_port[switch][mac_src]['porta'] == circuito_conf['porta_saida'] and switch == circuito_conf['switch_saida'] and self.mac_to_port[switch][mac_src]['vlan_id'] == circuito_conf['vlan_id_saida']:
                                # Identifica a direcao 2 => 1
                                self.logger.debug('Direction (2) => (1) identified.')
                                switch_destino = circuito_conf['switch_entrada']
                                self.logger.debug('Destination switch is: %s.', str(switch_destino))
                                direcao = '2_to_1'

                    continuacao = False
                    if switch_destino != 0:
                        self.logger.info('Destination switch is different of 0. Continuing handling. Destination switch: %s', str(switch_destino))
                        continuacao = True
                    else:
                        self.logger.info('Destination switch is equal to 0. Stopping handling and looking for other circuit.')
                        continue

                    if continuacao:
                        vlan_id_range_in_check = False
                        vlan_id_in_check = False
                        vlan_id_range_out_check = False
                        vlan_id_out_check = False
                        try:
                            if vlan_id in circuito_conf['vlan_id_entrada']:
                                vlan_id_range_in_check = True
                        except TypeError:
                            vlan_id_in_check = True
                        try:
                            if vlan_id in circuito_conf['vlan_id_saida']:
                                vlan_id_range_out_check = True
                        except TypeError:
                            vlan_id_out_check = True

                        if direcao == '1_to_2':
                            self.logger.info('Direction is (1) => (2).')
                            #if ((vlan_id == circuito_conf['vlan_id_entrada']) or (vlan_id in circuito_conf['vlan_id_entrada']))  and (in_port == circuito_conf['porta_entrada']) and (dpid == circuito_conf['switch_entrada']):
                            if (vlan_id_range_in_check or vlan_id_in_check)  and (in_port == circuito_conf['porta_entrada']) and (dpid == circuito_conf['switch_entrada']):
                                #Caso de entrada pelo switch_entrada
                                self.logger.info('VLAN_ID of the packet is equal to input_VLAN_ID(id_pe_1) from configuration database and packet entering on the input switch of the circuit.')
                                try:
                                    self.logger.info('Topology object is: %s.', str(self.obj_topologia))
                                    self.logger.info('Routing table is: %s.', str(self.tabela_roteamento.tabela_roteamento_completa))
                                except AttributeError:
                                    self.logger.info('ERROR of routing table. Routing table is None. Possibly switches are not interconnected in the topology.')
                                try:
                                    out_port = int(self.tabela_roteamento.tabela_roteamento_completa[switch_origem][switch_destino][0].porta_saida)
                                    self.logger.info('Out port is: %s.', str(out_port))
                                except AttributeError:
                                    self.logger.info('ERROR calculating out_port. Possibly LLDP tagged topology discovery is not working. Exiting vlan_handler.')
                                    self.lldp_discovery_verification(False)
                                    return
                                except KeyError:
                                    self.logger.info('ERROR calculating out_port. Possibly topology object is NOT correct and routing table is NOT full.')
                                    return
                                vlan_vid_modified = int(circuito_ident['id_backbone'])
                                self.logger.info('VLAN ID to be modified is: %s.', str(vlan_vid_modified))
                                try: 
                                    if vlan_id in circuito_conf['vlan_id_entrada']: #Garante que e o caso de VLAN range
                                        vlan_vid_modified_last_switch = vlan_id #Utilizada no caso de vlan range, no ultimo switch na direcao 1_to_2 o vlan_id de saida seja igual ao vlan_id de entrada
                                except Exception:
                                    pass

                                modificacao_vlan_vid = True
                                instalacao_proativa = True
                                break_circuit_ident_loop = True
                                break

                            elif vlan_id == circuito_ident['id_backbone']: #Somente entra quando nao entrou no primeiro if
                                self.logger.info('VLAN_ID of the packet is equal to the backbone VLAN ID for the circuit.')
                                self.logger.info('Routing table is: %s.', str(self.tabela_roteamento.tabela_roteamento_completa))
                                self.logger.info('Topology object is: %s.', str(self.obj_topologia))
                                if switch_origem == switch_destino:
                                    # Caso do ultimo switch na direcao 1_to_2
                                    self.logger.info('Packet entering on the exiting switch of the circuit (last switch on the direction (1) => (2)). Destination switch: %s', str(switch_destino))
                                    out_port = int(circuito_conf['porta_saida'])
                                    self.logger.info('Out port is: %s.', str(out_port))
                                    if vlan_vid_modified_last_switch != 0: #Entra no caso de VLAN range
                                        vlan_vid_modified = vlan_vid_modified_last_switch
                                    else:
                                        vlan_vid_modified = int(circuito_conf['vlan_id_saida'])
                                    self.logger.info('VLAN ID to be modified is: %s.', str(vlan_vid_modified))
                                    modificacao_vlan_vid = True
                                    break_circuit_ident_loop = True
                                    break

                                else:
                                    # Caso de transporte pelo backbone
                                    self.logger.info('Packet entering on any switch of the direction (1) => (2) of the circuit.')
                                    try:
                                        out_port = int(self.tabela_roteamento.tabela_roteamento_completa[switch_origem][switch_destino][0].porta_saida)
                                        self.logger.info('Out port is: %s.', str(out_port))
                                    except AttributeError:
                                        self.logger.info('ERROR calculating out_port. Possibly LLDP tagged topology discovery is not working. Exiting vlan_handler.')
                                        self.lldp_discovery_verification(False)
                                        return
                                    except KeyError:
                                        self.logger.info('ERROR calculating out_port. Possibly topology object is NOT correct and routing table is NOT full.')
                                        return

                                    modificacao_vlan_vid = False
                                    break_circuit_ident_loop = True
                                    break

                        elif direcao == '2_to_1':
                            self.logger.info('Direction is (2) => (1).')
                            #if ((vlan_id == circuito_conf['vlan_id_saida']) or (vlan_id in circuito_conf['vlan_id_saida']))  and (in_port == circuito_conf['porta_saida']) and (dpid == circuito_conf['switch_saida']):
                            if (vlan_id_range_out_check or vlan_id_out_check) and (in_port == circuito_conf['porta_saida']) and (dpid == circuito_conf['switch_saida']):
                                # Caso de entrada pelo switch_saida
                                self.logger.info('VLAN_ID of the packet is equal to output_VLAN_ID(id_pe_2) from configuration database and packet entering on the exiting switch of the circuit.')
                                try:
                                    self.logger.info('Topology object is: %s.', str(self.obj_topologia))
                                    self.logger.info('Routing table is: %s.', str(self.tabela_roteamento.tabela_roteamento_completa))
                                except AttributeError:
                                    self.logger.info('ERROR of routing table. Routing table is None. Possibly switches are not interconnected in the topology.')
                                try:
                                    out_port = int(self.tabela_roteamento.tabela_roteamento_completa[switch_origem][switch_destino][0].porta_saida)
                                    self.logger.info('Out port is: %s.', str(out_port))
                                except AttributeError:
                                    self.logger.info('ERROR calculating out_port. Exiting vlan_handler.')
                                    self.lldp_discovery_verification(False)
                                    return
                                except KeyError:
                                    self.logger.info('ERROR calculating out_port. Possibly topology object is NOT correct and routing table is NOT full.')
                                    return
                                vlan_vid_modified = int(circuito_ident['id_backbone'])
                                self.logger.info('VLAN ID to be modified is: %s.', str(vlan_vid_modified))
                                try:
                                    if vlan_id in circuito_conf['vlan_id_entrada']: #Garante que e o caso de VLAN range
                                        vlan_vid_modified_last_switch = vlan_id #Utilizada no caso de vlan range, no ultimo switch na direcao 1_to_2 o vlan_id de saida seja igual ao vlan_id de entrada
                                except Exception:
                                    pass
                                modificacao_vlan_vid = True
                                instalacao_proativa = True
                                break_circuit_ident_loop = True
                                break

                            elif vlan_id == circuito_ident['id_backbone']: #So entra se nao tiver entrado no primeiro if
                                self.logger.info('VLAN_ID of the packet is equal to the backbone VLAN ID for the circuit.')
                                self.logger.info('Routing table is: %s.', str(self.tabela_roteamento.tabela_roteamento_completa))
                                self.logger.info('Topology object is: %s.', str(self.obj_topologia))
                                if switch_origem == switch_destino:
                                    # Caso do ultimo switch na direcao 2_to_1
                                    self.logger.info('Packet entering on the entering switch of the circuit (last switch on the direction (2) => (1)). Destination switch: %s', str(switch_destino))
                                    out_port = int(circuito_conf['porta_entrada'])
                                    self.logger.info('Out port is: %s.', str(out_port))
                                    if vlan_vid_modified_last_switch != 0: #Entra no caso de VLAN range
                                        vlan_vid_modified = vlan_vid_modified_last_switch
                                    else:
                                        vlan_vid_modified = int(circuito_conf['vlan_id_entrada'])
                                    self.logger.info('VLAN ID to be modified is: %s.', str(vlan_vid_modified))
                                    modificacao_vlan_vid = True
                                    break_circuit_ident_loop = True
                                    break

                                else:
                                    # Caso de transporte pelo backbone
                                    self.logger.info('Packet entering on any switch of the direction (2) => (1) of the circuit.')
                                    try:
                                        out_port = int(self.tabela_roteamento.tabela_roteamento_completa[switch_origem][switch_destino][0].porta_saida)
                                        self.logger.info('Out port is: %s.', str(out_port))
                                    except AttributeError:
                                        self.logger.info('ERROR calculating out_port. Exiting vlan_handler.')
                                        self.lldp_discovery_verification(False)
                                        return
                                    except KeyError:
                                        self.logger.info('ERROR calculating out_port. Possibly topology object is NOT correct and routing table is NOT full.')
                                        return
                                    modificacao_vlan_vid = False
                                    break_circuit_ident_loop = True
                                    break

            if break_circuit_ident_loop:
                break # Break circuit configuration loop

        self.logger.info('Source switch is: %s and destination switch is: %s.', str(switch_origem), str(switch_destino))
        try:
        #Colocar um espaco
            #REMOVER PARA EMULACAO
            """
            try:
                # Switches OpenFlow 1.0
                match = ofp_parser.OFPMatch(in_port=in_port, dl_src=haddr_to_bin(mac_src), dl_vlan=vlan_id)
                openflow_version = '1.0'
                self.logger.info('Match for OF1.0: %s.', str(match))
            except KeyError:
                # Switches OpenFlow 1.3
                match = ofp_parser.OFPMatch(in_port=in_port, eth_src=mac_src, vlan_vid=vlan_id)
                openflow_version = '1.3'
                self.logger.info('Match for OF1.3: %s.', str(match))
            """
            #FIM DA REMOCAO PARA EMULACAO
            #ADICAO PARA EMULACAO | MODIFICADO PARA TESTE FIBRE
            try:
                # Switches OpenFlow 1.0
                match = ofp_parser.OFPMatch(in_port=in_port, dl_src=haddr_to_bin(mac_src))
                openflow_version = '1.0'
                self.logger.debug('Match for OF1.0: %s.', str(match))
            except KeyError:
                # Switches OpenFlow 1.3
                match = ofp_parser.OFPMatch(in_port=in_port, eth_src=mac_src)
                openflow_version = '1.3'
                self.logger.debug('Match for OF1.3: %s.', str(match))
            #FIM DA ADICAO PARA EMULACAO | FIM DA MODIFICACAO PARA TESTE FIBRE

            try:
                self.logger.info('Match is: %s.', str(match))
                self.logger.info('Out port is: %s.', str(out_port))
            except UnboundLocalError:
                self.logger.info('Unable to calculate out_port')
                return

            if modificacao_vlan_vid:
                #REMOCAO PARA EMULACAO | MODIFICADO PARA TESTE FIBRE
                """
                if openflow_version == '1.0': # Teve de ser feito utilizando laco IF pois o campo dl_vlan no SetField nao reporta erro quando o datapath e OpenFlow 1.3
                    #Switches OpenFlow 1.0
                    actions = [ofp_parser.OFPActionVlanVid(vlan_vid=vlan_vid_modified),ofp_parser.OFPActionOutput(out_port)]
                else:
                    #Switches OpenFlow 1.3 or higher
                    actions = [ofp_parser.OFPActionSetField(vlan_vid=vlan_vid_modified),ofp_parser.OFPActionOutput(out_port)]
                """
                #FIM DA REMOCAO PARA EMULACAO | FIM DA MODIFICACAO PARA TESTE FIBRE
                #ADICAO PARA EMULACAO | MODIFICADO PARA TESTE FIBRE
                actions = [ofp_parser.OFPActionOutput(out_port)]
                #FIM DA ADICAO PARA EMULACAO | FIM DA MODIFICACAO PARA TESTE FIBRE
            else:
                actions = [ofp_parser.OFPActionOutput(out_port)]

            self.logger.info('Actions is: %s.', str(actions))
            # Evita que regras indevidas sejam instaladas quando o roteador apresenta comportamento indevido
            if in_port == out_port:
                self.logger.info('in_port: %s is equal to out_port: %s. Returning from vlan_handler.', str(in_port), str(out_port))
                return

            self.add_flow(datapath, 2, match, actions)
            #self.add_flow(datapath, 1, match, actions2)

            #O PACKET-OUT nao modifica a tag de VLAN entao retiramos o PACKET-OUT
            #self.packet_out(datapath, msg, in_port, actions)

        #except UnboundLocalError:
        #    self.logger.info('Unable to find the correct out_port. Possible error on the configuration database or mismatch on SetField Action on the previous switch.')

        except Exception:
            self.logger.info('ERROR dealing with VLAN ID: %s on in_port: %s', str(vlan_id), str(in_port))
            #raise

	#SPLIT HORIZON
        for interface in self.switches_in_topology[dpid][1::]:
            try:
                if in_port != interface['port_no']:
                    drop_in_port = interface['port_no']
                    if modificacao_vlan_vid:
                        try:
                            # Switches OpenFlow 1.0
                            match = ofp_parser.OFPMatch(in_port=drop_in_port, dl_src=haddr_to_bin(mac_src), dl_vlan=vlan_vid_modified)
                        except KeyError:
                            # Switches OpenFlow 1.3
                            match = ofp_parser.OFPMatch(in_port=drop_in_port, eth_src=mac_src, vlan_vid=vlan_vid_modified)
                    else:
                        try:
                            # Switches OpenFlow 1.0
                            match = ofp_parser.OFPMatch(in_port=drop_in_port, dl_src=haddr_to_bin(mac_src), dl_vlan=vlan_id)
                        except KeyError:
                            # Switches OpenFlow 1.3
                            match = ofp_parser.OFPMatch(in_port=drop_in_port, eth_src=mac_src, vlan_vid=vlan_id)
                    actions = []
                    self.logger.info('Installing split horizon flows for DPID: %s, match: %s', str(dpid), str(match))
                    self.add_flow(datapath, 1, match, actions)
            except Exception:
                self.logger.info('ERROR dealing with split horizon for DPID: %s', str(dpid))
	# FIM SPLIT HORIZON

        """
        if instalacao_proativa:
            # Realiza a instalacao proativa na direcao 1_to_2 ou 2_to_1 no packet_in no primeiro switch do circuito
            next_dpid = self.tabela_roteamento.tabela_roteamento_completa[switch_origem][switch_destino][1].switch
            next_vlan_id = vlan_vid_modified
            next_datapath = self.dictionary_dpid_datapath[next_dpid]
            next_in_port = self.obj_topologia[switch_origem][next_dpid].int_entrada_dst # Representa a in_port do next_dpid do objeto de topologia que cada entrada e um objeto do tipo portas
            self.logger.info('Instaling flow entries proactively for DPID: %s, VLAN ID: %s, in_port: %s.', str(next_dpid), str(next_vlan_id), str(next_in_port))
            self.vlan_handler(next_datapath, next_vlan_id, next_in_port, mac_src)
        """

        if instalacao_proativa:
            #FAZER LOOP NOS OBJETOS DA CLASSE SAIDA INSTALANDO REGRA EM TODOS
            #print "*** Realizando instalacao proativa. Sw ori: ", switch_origem, " Sw dest: ", switch_destino
            #print "O objeto topologia e: ", self.obj_topologia
            for salto in self.tabela_roteamento.tabela_roteamento_completa[switch_origem][switch_destino]:
		#print "O salto analisado e: ", salto
                if salto.num_salto != 0:
                    # Realiza a instalacao proativa na direcao 1_to_2 ou 2_to_1 no packet_in no primeiro switch do circuito
                    next_dpid = salto.switch
                    #print "O next_dpid e: ", next_dpid, "e o tipo e: ", type(next_dpid)
                    next_vlan_id = vlan_vid_modified
                    next_datapath = self.dictionary_dpid_datapath[next_dpid]
                    next_in_port = self.obj_topologia[salto_anterior.switch][next_dpid].int_entrada_dst # Representa a in_port do next_dpid do objeto de topologia que cada entrada e um objeto do tipo portas
                    self.logger.info('Instaling flow entries proactively for DPID: %s, VLAN ID: %s, in_port: %s.', str(next_dpid), str(next_vlan_id), str(next_in_port))
                    if salto.porta_saida == None and vlan_vid_modified_last_switch != 0: #Utilizada no caso de vlan range, no ultimo switch na direcao 1_to_2 o vlan_id de saida seja igual ao vlan_id de entrada
                        self.vlan_handler(next_datapath, next_vlan_id, next_in_port, mac_src, vlan_vid_modified_last_switch)
                    else:
                        self.vlan_handler(next_datapath, next_vlan_id, next_in_port, mac_src)

                    if salto.porta_saida == None: # Indica ser o endpoint do circuito virtual (utilizado para contabilizar o tempo de processamento de mensagens de sinalizacao)
                        self.logger.info('Endpoint of the virtual circuit, calculating flow_mod.')
                salto_anterior = salto # Quando num_salto for igual a 0 o salto_anterior vai ser o switch_origem

        return


    @set_ev_cls(ofp_event.EventOFPDescStatsReply, MAIN_DISPATCHER)
    def desc_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id

        desc_dictionary = {'mfr_desc': body.mfr_desc, 'hw_desc': body.hw_desc, 'sw_desc': body.sw_desc, 'serial_num': body.serial_num, 'dp_desc': body.dp_desc, 'dp_id': dpid}

        self.logger.info('OFPDescStatsReply DPID: %s received dictionary: %s', str(ev.msg.datapath.id), str(desc_dictionary))

        if self.switches_in_topology.has_key(ev.msg.datapath.id):
            if str(self.switches_in_topology[ev.msg.datapath.id][0]) != str(desc_dictionary) :
                self.switches_in_topology[ev.msg.datapath.id][0] = desc_dictionary
                self.switches_in_topology_file[desc_dictionary['dp_desc']] = {'Description': desc_dictionary, 'Interfaces': self.switches_in_topology[dpid][1::]}
                file_interfaces = open(self.interfaces_info, 'w')
                json.dump(self.switches_in_topology, file_interfaces, sort_keys=True, indent=4, separators=(',',': '))
                file_interfaces.close()
                self.logger.info('Global Dictionary {DPID: [sw_desc, <ports>]} for DPID: %s has been UPDATED: %s', str(ev.msg.datapath.id), str(self.switches_in_topology))
                file_interfaces = open(self.interfaces_info_object, 'w')
                json.dump(self.switches_in_topology_file, file_interfaces, sort_keys=True, indent=4, separators=(',',': '))
                file_interfaces.close()

            else:
                self.logger.info('Global Dictionary {DPID: [sw_desc, <ports>]} for DPID: %s has NOT been UPDATED: %s', str(ev.msg.datapath.id), str(self.switches_in_topology))
        else:
            self.switches_in_topology[ev.msg.datapath.id] = []
            self.switches_in_topology[ev.msg.datapath.id].append(desc_dictionary)
            self.logger.info('Global Dictionary {DPID: [sw_desc, <ports>]} for DPID: %s has been CREATED: %s', str(ev.msg.datapath.id), str(self.switches_in_topology))

        return


    #@set_ev_cls(stplib.EventPacketIn, MAIN_DISPATCHER)
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        # Referencia para o learning switch que aprende as portas dos hosts e: https://osrg.github.io/ryu-book/en/html/switching_hub.html
        msg = ev.msg
        dp = msg.datapath
        ofp = dp.ofproto
        ofp_parser = dp.ofproto_parser
        dpid = dp.id
        self.logger.debug('Packet-in received.')

        pkt = packet.Packet(msg.data)
        pkt_ethernet = pkt.get_protocol(ethernet.ethernet)
        pkt_llc = pkt.get_protocol(llc.llc)
        pkt_arp = pkt.get_protocol(arp.arp)
        pkt_vlan = pkt.get_protocol(vlan.vlan)
        pkt_ipv4 = pkt.get_protocol(ipv4.ipv4)

        mac_dst = pkt_ethernet.dst
        mac_src = pkt_ethernet.src

	try:
            # Switches OpenFlow 1.0
            in_port = msg.in_port

        except AttributeError:
            # Switches OpenFlow 1.3
            in_port = msg.match['in_port']

        self.s_tabela_roteamento.acquire()

	#MODIFICAR LINHA ABAIXO PARA EMULACAO (if)
        if pkt_vlan and not pkt_llc:
        #if pkt_vlan and pkt_vlan.ethertype != ether_types.ETH_TYPE_LLDP and not pkt_llc:
            self.logger.info('VLAN packet received by DPID %s', str(dpid))
            self.logger.info('VLAN ethertype is: %s', str(pkt_vlan.ethertype))

        #ALTERADO PARA A EMULACAO (linha abaixo)
        elif pkt_ethernet and pkt_ethernet.ethertype == ether_types.ETH_TYPE_LLDP:
        #elif pkt_vlan and pkt_vlan.ethertype == ether_types.ETH_TYPE_LLDP:
            self.logger.debug('LLDP packet received. Exiting PACKET_IN_HANDLER.')
            self.logger.debug('Packet received by DPID: %s. MAC source is: %s. In_port is: %s. MAC destination is: %s.', str(dpid), str(mac_src), str(in_port), str(mac_dst))
            self.s_tabela_roteamento.release()
            return

        #RETIRADO PARA TESTAR SE ACEITANDO OS PACOTES LLC O TRAFEGO E ENCAMINHADO A PARTIR DO SWITCH
        elif pkt_llc:
            self.logger.debug('LLC packet received. Analising flow to drop LLC and exiting PACKET_IN_HANDLER. Just flows that comes from endpoint AC are droped.')
            self.llc_drop_handler(dp, msg, in_port, mac_dst)
            self.s_tabela_roteamento.release()
            return

        for p in pkt:
            try:
                self.logger.debug('Header type received is: %s.', str(p.protocol_name))

                if p.protocol_name == 'ethernet':
                    self.logger.debug('ETHERNET header: %s.', str(p))
                elif p.protocol_name == 'vlan':
                    self.logger.debug('VLAN (802.1q) header: %s.', str(p))
                elif p.protocol_name == 'arp':
                    self.logger.debug('ARP header: %s.', str(p))
                elif p.protocol_name == 'icmp':
                    self.logger.debug('ICMP header: %s.', str(p))

            except Exception:
                pass

        self.logger.info('Packet received by DPID: %s. MAC source is: %s. In_port is: %s. MAC destination is: %s.', str(dpid), str(mac_src), str(in_port), str(mac_dst))
	
	#Verifica se o pacote e recebido em um endpoint. Caso nao seja, nao e realizado nenhum tratamento. Com isso e possivel que a regra no endpoint pare e seja novamente recebido um Packet-In
	pacote_recebido_endpoint_caracteristica_completa = False
	for circuito_conf in self.dicionario_l2vpn:
	    if (circuito_conf['switch_entrada'] == dpid and circuito_conf['porta_entrada'] == in_port and circuito_conf['vlan_id_entrada'] == pkt_vlan.vid) or (circuito_conf['switch_saida'] == dpid and circuito_conf['porta_saida'] == in_port and circuito_conf['vlan_id_saida'] == pkt_vlan.vid ):
		pacote_recebido_endpoint_caracteristica_completa = True
		
	if not pacote_recebido_endpoint_caracteristica_completa: #Caso em que o pacote e recebido em um switch que nao e endpoint
	    self.logger.info('Packet has not been received in an endpoint. Exiting packet_in_handling.')
	    self.s_tabela_roteamento.release()
	    return


        self.timestamp_novo_mac_src[mac_src] = time()
        continuacao = "sim" #Utilizado para fazer aprendizado ou renovacao de MAC source
        # Pode verificar se o MAC src ja esta vinculado a um switch
        # O problema com essa abordagem e que o host fica sempre amarrado ao switch. Na tabela mac_to_port o mac do host sempre estara vinculado ao switch ao qual estava conectado inicialmente
        for switch_id in self.mac_to_port:
            if self.timestamp_antigo_mac_src.has_key(mac_src):
                if self.mac_to_port[switch_id].has_key(mac_src) and ( (self.timestamp_novo_mac_src[mac_src] - self.timestamp_antigo_mac_src[mac_src]) < self.intervalo_renovacao_mac_src ):
                    continuacao = "nao"
        self.timestamp_antigo_mac_src[mac_src] = self.timestamp_novo_mac_src[mac_src]

        # Aprende o endereco MAC para evitar o FLOOD da proxima vez
        if (continuacao == "sim"):
            if self.mac_to_port.has_key(dpid):
                if (self.mac_to_port[dpid].has_key(mac_src)):
                    if (self.mac_to_port[dpid][mac_src]['porta'] != in_port):
                        self.mac_to_port[dpid][mac_src]['porta'] = in_port
                        self.mac_to_port[dpid][mac_src]['vlan_id'] = pkt_vlan.vid
                        self.logger.info('MAC_to_port table MAC: %s, DPID: %s, has been updated to port: %s. MAC_to_port UPDATED to: %s', str(mac_src), str(dpid), str(in_port), str(self.mac_to_port))
                else:
                    for switch in self.mac_to_port.keys(): #Verifica se o MAC que ja tem na tabela foi alterado de switch. Se ja houver ele deleta
                        if self.mac_to_port[switch].has_key(mac_src):
                            del self.mac_to_port[switch][mac_src]
                    self.mac_to_port[dpid][mac_src] = {}
                    self.mac_to_port[dpid][mac_src]['porta'] = in_port
                    self.mac_to_port[dpid][mac_src]['vlan_id'] = pkt_vlan.vid
                    self.logger.info('MAC_to_port table MAC: %s, DPID: %s, has been created port: %s. MAC_to_port UPDATED to: %s', str(mac_src), str(dpid), str(in_port), str(self.mac_to_port))
            else:
                self.mac_to_port[dpid] = {}
                self.mac_to_port[dpid][mac_src] = {}
                self.mac_to_port[dpid][mac_src]['porta'] = in_port
                self.mac_to_port[dpid][mac_src]['vlan_id'] = pkt_vlan.vid
                self.logger.info('MAC_to_port table for DPID: %s, has been created MAC: %s, port: %s. MAC_to_port updated to: %s', str(dpid), str(mac_src), str(in_port), str(self.mac_to_port))

        if pkt_vlan:
            self.logger.info('VLAN tagged packet received: %s. Initiating handling.', str(pkt_vlan))
            #self.vlan_handler(dp, msg, pkt_vlan, in_port, mac_src)
            self.vlan_handler(dp, pkt_vlan.vid, in_port, mac_src)

        self.s_tabela_roteamento.release()
        return


    def flow_entries_deletion_full(self):
        for dpid in self.dictionary_dpid_datapath.keys():
            datapath = self.dictionary_dpid_datapath[dpid]
            ofp = datapath.ofproto
 
            self.logger.debug('Match actions dictionary is: %s.', str(self.dpid_match_actions_dic))
            if self.dpid_match_actions_dic.has_key(dpid):
                self.logger.debug('List of match actions dictionaries for DPID: %s is: %s.', str(dpid), str(self.dpid_match_actions_dic[dpid])) 
                for actions_match_dic in self.dpid_match_actions_dic[dpid]:
                    actions = actions_match_dic['actions']
                    match = actions_match_dic['match']
                    self.mod_flow(datapath, 1, match, actions, ofp.OFPFC_DELETE)

            else:
                continue

        self.dpid_match_actions_dic = {}
        self.logger.info('Match and actions dictionary has been RESETED: %s.', str(self.dpid_match_actions_dic))

        return

    @handler.set_ev_cls(event.EventLinkAdd)
    def link_add_handler(self, ev):
        self.last_link_add = time()
        switch_src = ev.link.src.dpid
        switch_dest = ev.link.dst.dpid
        port_src = ev.link.src.port_no
        port_src_original = port_src
        port_src_name = ev.link.src.name
        port_dest = ev.link.dst.port_no
        port_dest_name = ev.link.dst.name
        port_dest_original = port_dest
        self.logger.info('Link added. SW src: %s, port_src: %s, port_src_name: %s | SW dst: %s, port_dest: %s, port_dest_name: %s', str(switch_src), str(port_src), str(port_src_name), str(switch_dest), str(port_dest), str(port_dest_name))
        self.s_obj_topologia.acquire()
        self.s_obj_topologia_auxiliar.acquire()
        self.s_tabela_roteamento.acquire()

        #RETIRADA PARA EMULACAO
        """
        #INICIAR O COMENTARIO PARA TIRAR DUAS INTERFACES AQUI
        #O padrao é utilizar as subinterface .10 com encapsulation dot1q any, porém o ASR9K-CBPF não encaminha as mensagens LLDP nesse caso. Então é necessário criar uma outra subinterface .11 com encapsulation priority-tagged para tratar os pacotes LLDP. Assim, o tratamento abaixo é para quando uma subinterface .10 se conectar a uma .11, o controlador associe a interface .10.
        port_src_name_int_subint = {'interface': port_src_name.split(".")[0], 'sub_interface': port_src_name.split(".")[1]}
        port_dest_name_int_subint = {'interface': port_dest_name.split(".")[0], 'sub_interface': port_dest_name.split(".")[1]}

        if port_src_name_int_subint['sub_interface'] == "10" or port_dest_name_int_subint['sub_interface'] == "10":
            self.logger.info('Subinterface of port source or destination of link add is .10 (encapsulation dot1q any). Link NOT ADDED. Exiting link_add_handler.')
            self.s_obj_topologia.release()
            self.s_obj_topologia_auxiliar.release()
            self.s_tabela_roteamento.release()
            return

        elif port_src_name_int_subint['sub_interface'] == "11" and port_dest_name_int_subint['sub_interface'] == "11":
            self.logger.info('Both subinterfaces are .11 (encapsulation dot1q priority-tagged). Link priority tagged added at')
            if self.switches_in_topology.has_key(switch_src):
                self.logger.debug('self.switches_in_topology has switch_src: %s.', str(switch_src))
                for port in self.switches_in_topology[switch_src]:
                    #print "Entrando no for para porta: ", port
                    try:
                        #print "Antes do if de comparacao de portas para port['name]: ", port['name'], "e port_src_name_int_subint['interface'] + .10: ", port_src_name_int_subint['interface'] + ".10"
                        if port['name'] == (port_src_name_int_subint['interface'] + ".10"):
                            # print "O port name e igual, modificando o port_src"
                            port_src = port['port_no']
                            #print "O novo port_src e: ", port_src
                    except Exception:
                        pass

            if self.switches_in_topology.has_key(switch_dest):
                #print "self.switches_in_topology possui switch_dest: ", switch_dest
                for port in self.switches_in_topology[switch_dest]:
                    #print "Entrando no for para porta: ", port
                    try:
                        #print "Antes do if de comparacao de portas para port['name]: ", port['name'], "e port_src_name_int_subint[    'interface'] + .10: ", port_dest_name_int_subint['interface'] + ".10"
                        if port['name'] == (port_dest_name_int_subint['interface'] + ".10"):
                            #print "O port name e igual, modificando o port_dest"
                            port_dest = port['port_no']
                            #print "O novo port_dest e: ", port_dest
                    except Exception:
                        pass
        """
        #FIM DA RETIRADA PARA EMULACAO
        #FINALIZAR O COMENTARIO PARA TIRAR DUAS INTERFACES AQUI

	# INICIO DO TRATAMENTO DE MULTIPLOS LINKS ENTRE DOIS SWITCHES
        self.obj_topologia_auxiliar.append({switch_src:{switch_dest: portas(port_src,port_dest)}})
        self.logger.info('Auxiliary Topology object for link: %s, has been UPDATED. Auxiliary Topology object: %s', str({switch_src:{switch_dest: portas(port_src,port_dest)}}), str(self.obj_topologia_auxiliar))
        # Gera o dicionario dicionario_sw_ori_sw_dest que contem as portas de entrada e saida dos switches destino e origem, respectivamente, para poder checar se ha simetria nos enlaces
        dicionario_sw_ori_sw_dest = {'direto': [], 'inverso': []}
        for posicao_enlace in range(len(self.obj_topologia_auxiliar)):
            if self.obj_topologia_auxiliar[posicao_enlace].has_key(switch_src):
                if self.obj_topologia_auxiliar[posicao_enlace][switch_src].has_key(switch_dest):
                    dicionario_sw_ori_sw_dest['direto'].append(self.obj_topologia_auxiliar[posicao_enlace][switch_src][switch_dest])

            if self.obj_topologia_auxiliar[posicao_enlace].has_key(switch_dest):
                if self.obj_topologia_auxiliar[posicao_enlace][switch_dest].has_key(switch_src):
                    dicionario_sw_ori_sw_dest['inverso'].append(self.obj_topologia_auxiliar[posicao_enlace][switch_dest][switch_src])
        # Altera o objeto obj_topologia se houver simetria nos enlaces (no obj_topologia_auxiliar). No momento em que a rede esta convergindo enquanto nao ha bloqueio de portas ha multiplas atualizacoes do obj_topologia
        for enlace_direto in dicionario_sw_ori_sw_dest['direto']:
            for enlace_inverso in dicionario_sw_ori_sw_dest['inverso']:
                if enlace_direto.int_saida_src == enlace_inverso.int_entrada_dst and enlace_direto.int_entrada_dst == enlace_inverso.int_saida_src:
                    if self.obj_topologia.has_key(switch_src):
                        self.obj_topologia[switch_src][switch_dest] = enlace_direto
                    else:
                        self.obj_topologia[switch_src] = {}
                        self.obj_topologia[switch_src][switch_dest] = enlace_direto
                    if self.obj_topologia.has_key(switch_dest):
                        self.obj_topologia[switch_dest][switch_src] = enlace_inverso
                    else:
                        self.obj_topologia[switch_dest] = {}
                        self.obj_topologia[switch_dest][switch_src] = enlace_inverso

                    self.logger.info('Topology object for switch source: %s and switch destination: %s, has been UPDATED (multiple links between same switches). Topology object: %s', str(switch_src), str(switch_dest), str(self.obj_topologia))
        # FIM DO TRATAMENTO DE MULTIPLOS LINKS ENTRE DOIS SWITCHES

        self.tabela_roteamento = calculo_dijkstra(self.obj_topologia)
        self.logger.info('Routing table updated after link add at: %f : Routing table: %s', time(), str(self.tabela_roteamento))

        if self.switches_in_topology.has_key(switch_src):
            for i in range(len(self.switches_in_topology[switch_src])):
                try:
                    if self.switches_in_topology[switch_src][i]['port_no'] == port_src:
                        self.switches_in_topology[switch_src][i]['link_state'] = 'UP'
                except Exception:
                    pass

        """ADICIONADO PARA VERIFICAR O FUNCIONAMENTO"""
        """
        if self.switches_in_topology.has_key(switch_src):
            for i in range(len(self.switches_in_topology[switch_src])):
                try:
                    if self.switches_in_topology[switch_src][i]['port_no'] == port_src:
                        self.switches_in_topology[switch_src][i]['link_state'] = 'UP'
                except Exception:
                    pass
        """
        """FIM DO ADICIONADO PARA VERIFICACAO"""

        self.flow_entries_deletion_full()

        self.logger.info('Global Dictionary {DPID: [sw_desc, <ports>]} for DPIDs: %s and %s has been UPDATED: %s', str(switch_src), str(switch_dest), str(self.switches_in_topology))

        self.s_obj_topologia.release()
        self.s_obj_topologia_auxiliar.release()
        self.s_tabela_roteamento.release()
        return


    @handler.set_ev_cls(event.EventSwitchLeave)
    def switch_leave_handler(self, ev):
        try:
            switch_leave = ev.switch.dp.id
        except Exception: # Caso em que ocorre algum problema no framework que gera evento de switch_leave indevido
            return
        switches_sem_links = []
        posicoes_para_delete = []
        self.s_obj_topologia.acquire()
        self.s_obj_topologia_auxiliar.acquire()
        self.s_tabela_roteamento.acquire()
        self.logger.info('DPID: %s has left the topology', str(switch_leave))

        if self.obj_topologia.has_key(switch_leave): # deleta todas as entradas do obj_topologia que tem como switch_src o switch_leave
            del self.obj_topologia[switch_leave]

        for posicao in range(len(self.obj_topologia_auxiliar)):
            if self.obj_topologia_auxiliar[posicao].has_key(switch_leave): # deleta todas as entradas do obj_topologia_auxiliar que tem como switch_src o switch_leave
                posicoes_para_delete.append(posicao)

        posicoes_para_delete.sort(reverse=True)

        for posicao in posicoes_para_delete:
            del self.obj_topologia_auxiliar[posicao]

        if self.switches_in_topology.has_key(switch_leave):
            del self.switches_in_topology[switch_leave]
            file_interfaces = open(self.interfaces_info, 'w')
            json.dump(self.switches_in_topology, file_interfaces, sort_keys=True, indent=4, separators=(',',': '))
            file_interfaces.close()
            self.logger.info('Global Dictionary {DPID: [sw_desc, <ports>]} for DPID: %s has been UPDATED: %s', str(switch_leave), str(self.switches_in_topology))

        #Deleta as entradas em que o switch que saiu seja um switch de destino do objeto topologia    
        for port_leave in ev.switch.ports: # ev.switch.ports é uma lista que contem objetos que contem o id do switch e porta que foram desligados
            #print "A porta do switch que saiu e", str(port_leave)
            #print "A porta do switch que saiu e", ṕort_leave.port_no
            for sw_origem in self.obj_topologia: # Percorre as lista com os destino dos enlaces unidirecionais    
                if ( self.obj_topologia[sw_origem].has_key(switch_leave) ) and ( self.obj_topologia[sw_origem][switch_leave].int_entrada_dst == port_leave.port_no ):
                    del self.obj_topologia[sw_origem][switch_leave]

        # Deleta as entradas do objeto topologia que sao referentes a switches que nao tem nenhum link            
        for switch in self.obj_topologia:
            if ( self.obj_topologia[switch] == {} ):
                switches_sem_links.append(switch)

        for switch in switches_sem_links:
            del self.obj_topologia[switch]

        #self.switches.remove(switch_leave)

        #print "Os switches na topologia apos delecao de switch sao: ", self.switches

        self.flow_entries_deletion_full()

        self.logger.info('Topology Object updated after switch leave: %s', str(self.obj_topologia))

        self.tabela_roteamento = calculo_dijkstra(self.obj_topologia)
        self.logger.info('Routing table updated after switch leave: %s', str(self.tabela_roteamento))

        del self.dictionary_dpid_datapath[switch_leave]
        self.logger.info('List of dictionaries {DPID: datapath} has been updated: %s', str(self.dictionary_dpid_datapath))

        self.s_obj_topologia.release()
        self.s_obj_topologia_auxiliar.release()
        self.s_tabela_roteamento.release()
        return

    @handler.set_ev_cls(event.EventLinkDelete)
    def link_del_handler(self, ev):
        switch_src = ev.link.src.dpid
        switch_dest = ev.link.dst.dpid
        port_src = ev.link.src.port_no
        port_dest = ev.link.dst.port_no
        port_src_original = port_src
        port_src_name = ev.link.src.name
        port_dest_name = ev.link.dst.name
        port_dest_original = port_dest
        switches_sem_links = []
        self.s_obj_topologia.acquire()
        self.s_obj_topologia_auxiliar.acquire()
        self.s_tabela_roteamento.acquire()
        self.logger.info('Link deletion event. DPID src: %s, port_src: %s to DPID dst: %s, port_dst: %s has been deleted at', str(switch_src), str(port_src), str(switch_dest), str(port_dest))

	# RETIRADO PARA EMULACAO
        """
        port_src_name_int_subint = {'interface': port_src_name.split(".")[0], 'sub_interface': port_src_name.split(".")[1]}
        port_dest_name_int_subint = {'interface': port_dest_name.split(".")[0], 'sub_interface': port_dest_name.split(".")[1]}

        if port_src_name_int_subint['sub_interface'] == "10" or port_dest_name_int_subint['sub_interface'] == "10":
            self.logger.info('Subinterface of port source or destination of link add is .10 (encapsulation dot1q any). Link NOT DELETED. Exiting link_del_handler')
            self.s_obj_topologia.release()
            self.s_obj_topologia_auxiliar.release()
            self.s_tabela_roteamento.release()
            return

        elif port_src_name_int_subint['sub_interface'] == "11" and port_dest_name_int_subint['sub_interface'] == "11":
            self.logger.debug('Both subinterfaces are .11 (encapsulation dot1q priority-tagged).')
            if self.switches_in_topology.has_key(switch_src):
                self.logger.debug('self.switches_in_topology has switch_src: %s.', str(switch_src))
                for port in self.switches_in_topology[switch_src]:
                    #print "Entrando no for para porta: ", port
                    try:
                        #print "Antes do if de comparacao de portas para port['name]: ", port['name'], "e port_src_name_int       _subint['interface'] + .10: ", port_src_name_int_subint['interface'] + ".10"
                        if port['name'] == (port_src_name_int_subint['interface'] + ".10"):
                            # print "O port name e igual, modificando o port_src"
                            port_src = port['port_no']
                            #print "O novo port_src e: ", port_src
                    except Exception:
                        pass

            if self.switches_in_topology.has_key(switch_dest):
                #print "self.switches_in_topology possui switch_dest: ", switch_dest
                for port in self.switches_in_topology[switch_dest]:
                    #print "Entrando no for para porta: ", port
                    try:
                        #print "Antes do if de comparacao de portas para port['name]: ", port['name'], "e port_src_name_int       _subint[    'interface'] + .10: ", port_dest_name_int_subint['interface'] + ".10"
                        if port['name'] == (port_dest_name_int_subint['interface'] + ".10"):
                            #print "O port name e igual, modificando o port_dest"
                            port_dest = port['port_no']
                            #print "O novo port_dest e: ", port_dest
                    except Exception:
                        pass
        """
        #FIM DA RETIRADA PARA EMULACAO

        if self.obj_topologia.has_key(switch_src):
            self.logger.info('Topology object: %s has key switch source: %s.', str(self.obj_topologia), str(switch_src))
            if self.obj_topologia[switch_src].has_key(switch_dest):
                self.logger.info('Topology object for switch source: %s has key switch destination: %s.', str(self.obj_topologia[switch_src]), str(switch_dest))
                if ( self.obj_topologia[switch_src][switch_dest].int_saida_src == port_src ):
                    self.logger.info('Topology object for switch source: %s and switch destination: %s, %s is equal to port_src: %s', str(switch_src), str(switch_dest), str(self.obj_topologia[switch_src][switch_dest]), str(port_src))
                    if ( self.obj_topologia[switch_src][switch_dest].int_entrada_dst == port_dest ):
                        self.logger.info('Topology object for switch source: %s and switch destination: %s, %s is equal to port_dest: %s', str(switch_src), str(switch_dest), str(self.obj_topologia[switch_src][switch_dest]), str(port_dest))
                        self.logger.info('Deleting topology object: %s.', str(self.obj_topologia[switch_src][switch_dest]))
                        del self.obj_topologia[switch_src][switch_dest]

        #Como o STP bloqueia as interfaces no sentido TX somente, ao detectar uma queda de enlace unidirecional, o enlace nos dois sentidos e excluido para que o calculo da tabela de roteamento possa ocorrer. Isso nao impede que no atualizacao de topologia o link unidirecional ativo que foi excluido seja reaprendido visto que todas as interfaces entram em estado de aprendizado (LEARNING)	
        if self.obj_topologia.has_key(switch_dest):
            self.logger.info('Topology object: %s has key switch destination: %s.', str(self.obj_topologia), str(switch_dest))
            if self.obj_topologia[switch_dest].has_key(switch_src):
                self.logger.info('Topology object for switch destination: %s has key switch source: %s.', str(self.obj_topologia[switch_dest]), str(switch_src))
                if ( self.obj_topologia[switch_dest][switch_src].int_saida_src == port_dest ):
                    self.logger.info('Topology object for switch destination: %s and switch source: %s, %s is equal to port_dest: %s', str(switch_dest), str(switch_src), str(self.obj_topologia[switch_dest][switch_src]), str(port_dest))
                    if ( self.obj_topologia[switch_dest][switch_src].int_entrada_dst == port_src ):
                        self.logger.info('Topology object for switch destination: %s and switch source: %s, %s is equal to port_src: %s', str(switch_dest), str(switch_src), str(self.obj_topologia[switch_dest][switch_src]), str(port_src))
                        self.logger.info('Deleting topology object: %s.', str(self.obj_topologia[switch_dest][switch_src]))
                        del self.obj_topologia[switch_dest][switch_src]

        # INICIO DO TRATAMENTO PARA MULTIPLOS ENLACES ENTRE DOIS SWITCHES
        delecao_enlace_auxiliar_direto = False
        posicoes_a_serem_deletadas = []
        # Define as posicoes no obj_topologia_auxiliar que devem ser excluidos devido a delecao de enlace
        for posicao_enlace in range(len(self.obj_topologia_auxiliar)):
            if self.obj_topologia_auxiliar[posicao_enlace].has_key(switch_src):
                if self.obj_topologia_auxiliar[posicao_enlace][switch_src].has_key(switch_dest):
                    if str(self.obj_topologia_auxiliar[posicao_enlace][switch_src][switch_dest]) == str(portas(port_src, port_dest)):
                        posicoes_a_serem_deletadas.append(posicao_enlace)
                        delecao_enlace_auxiliar_direto = True
                        self.logger.info('Auxiliary Topology object for switch source: %s, port_src: %s, and switch destination: %s, port_dest: %s will be DELETED. Auxiliary Topology object: %s', str(switch_src), str(port_src), str(switch_dest), str(port_dest), str(self.obj_topologia_auxiliar))

        # Deleta as posicoes definidas anteriormente do obj_topologia_auxiliar
        for posicao_enlace in posicoes_a_serem_deletadas[::-1]:
            self.logger.info('Auxiliary Topology Object: %s. Deleting object:: %s.', str(self.obj_topologia_auxiliar), str(self.obj_topologia_auxiliar[posicao_enlace]))
            del self.obj_topologia_auxiliar[posicao_enlace]
        # Gera o dicionario dicionario_sw_ori_sw_dest que contem as portas de entrada e saida dos switches destino e origem, respectivamente, para poder checar se ha simetria nos enlaces
        dicionario_sw_ori_sw_dest = {'direto': [], 'inverso': []}
        for posicao_enlace in range(len(self.obj_topologia_auxiliar)):
            if self.obj_topologia_auxiliar[posicao_enlace].has_key(switch_src) and delecao_enlace_auxiliar_direto: # O delecao enlace_euxiliar_direto e para garantir que houve delecao do topologia auxiliar
                if self.obj_topologia_auxiliar[posicao_enlace][switch_src].has_key(switch_dest):
                    dicionario_sw_ori_sw_dest['direto'].append(self.obj_topologia_auxiliar[posicao_enlace][switch_src][switch_dest])

            if self.obj_topologia_auxiliar[posicao_enlace].has_key(switch_dest) and delecao_enlace_auxiliar_direto:
                if self.obj_topologia_auxiliar[posicao_enlace][switch_dest].has_key(switch_src):
                    dicionario_sw_ori_sw_dest['inverso'].append(self.obj_topologia_auxiliar[posicao_enlace][switch_dest][switch_src])
        # Altera o objeto obj_topologia se houver simetria nos enlaces (no obj_topologia_auxiliar). No momento em que a rede esta convergindo enquanto nao ha bloqueio de portas ha multiplas atualizacoes do obj_topologia
        for enlace_direto in dicionario_sw_ori_sw_dest['direto']:
            for enlace_inverso in dicionario_sw_ori_sw_dest['inverso']:
                if enlace_direto.int_saida_src == enlace_inverso.int_entrada_dst and enlace_direto.int_entrada_dst == enlace_inverso.int_saida_src:
                    if self.obj_topologia.has_key(switch_src):
                        self.obj_topologia[switch_src][switch_dest] = enlace_direto
                    else:
                        self.obj_topologia[switch_src] = {}
                        self.obj_topologia[switch_src][switch_dest] = enlace_direto
                    if self.obj_topologia.has_key(switch_dest):
                        self.obj_topologia[switch_dest][switch_src] = enlace_inverso
                    else:
                        self.obj_topologia[switch_dest] = {}
                        self.obj_topologia[switch_dest][switch_src] = enlace_inverso
                    self.logger.info('Topology object for switch source: %s and switch destination: %s, has been UPDATED (multiple links between same switches). Topology object: %s', str(switch_src), str(switch_dest), str(self.obj_topologia))
        # FIM DO TRATAMENTO PARA MULTIPLOS ENLACES ENTRE DOIS SWITCHES

        # Deleta as entradas do objeto topologia que sao referentes a switches que nao tem nenhum link                
        for switch in self.obj_topologia:
            if ( self.obj_topologia[switch] == {} ):
                switches_sem_links.append(switch)

        for switch in switches_sem_links:
            del self.obj_topologia[switch]

        self.logger.info('Topology Object updated after link deletion: %s', str(self.obj_topologia))

        self.tabela_roteamento = calculo_dijkstra(self.obj_topologia)
        self.logger.info('Routing table updated after link deletion at: %f : Routing table: %s', time(), str(self.tabela_roteamento))

        if self.switches_in_topology.has_key(switch_src):
            for i in range(len(self.switches_in_topology[switch_src])):
                try:
                    if self.switches_in_topology[switch_src][i]['port_no'] == port_src:
                        self.switches_in_topology[switch_src][i]['link_state'] = 'DOWN'
                except Exception:
                    pass

        if self.switches_in_topology.has_key(switch_dest):
            for i in range(len(self.switches_in_topology[switch_dest])):
                try:
                    if self.switches_in_topology[switch_dest][i]['port_no'] == port_dest:
                        self.switches_in_topology[switch_dest][i]['link_state'] = 'DOWN'
                except Exception:
                    pass

        self.flow_entries_deletion_full()

        self.logger.info('Global Dictionary {DPID: [sw_desc, <ports>]} for DPIDs: %s and %s has been UPDATED: %s', str(switch_src), str(switch_dest), str(self.switches_in_topology))

        self.s_obj_topologia.release()
        self.s_obj_topologia_auxiliar.release()
        self.s_tabela_roteamento.release()
        return


    @handler.set_ev_cls(event.EventSwitchEnter)
    def switch_enter_handler(self, ev):
        dic_dpid_datapath = {} #Dicionario contendo {DPID: datapath}
        ports = []
        switch_enter = ev.switch.dp.id

        self.logger.info('Switch DPID: %s connected to controller at ', str(switch_enter))
        self.dictionary_dpid_datapath[switch_enter] = ev.switch.dp
        self.logger.info('List of dictionaries {DPID: datapath} updated: %s', str(self.dictionary_dpid_datapath))

        for port in ev.switch.ports:
            state = None
            self.logger.debug('Switch port: %s', str(port))
            if port._state == 0:
                state = "ADMIN_UP"
            elif port._state == 1:
                state = "ADMIN_DOWN"
            self.logger.debug('Port_no: %s, hw_addr: %s, name: %s, state: %s', str(port.port_no), str(port.hw_addr), str(port.name), str(state))
            ports.append({'port_no': port.port_no, 'hw_addr': port.hw_addr, 'name': port.name, 'admin_state': state, 'link_state': None})

        self.switches_in_topology[switch_enter] = [None]
        for port in ports:
            self.switches_in_topology[switch_enter].append(port)

        self.logger.info('Global Dictionary {DPID: [sw_desc, <ports>]} for DPID: %s has been CREATED: %s', str(switch_enter), str(self.switches_in_topology))
        file_interfaces = open(self.interfaces_info, 'w')
        json.dump(self.switches_in_topology, file_interfaces, sort_keys=True, indent=4, separators=(',',': '))
        file_interfaces.close()

        return


    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
	# Utilizado para evitar erro de Broken Pipe quando os switches se desconectam do controlador
        datapath = ev.msg.datapath
        datapath.socket.settimeout(TIMEOUT_ECHO_REQUEST)
        return


    @handler.set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def port_status_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp = dp.ofproto
        state = None

        if msg.reason == ofp.OFPPR_ADD:
            reason = 'ADD'
        elif msg.reason == ofp.OFPPR_DELETE:
            reason = 'DELETE'
        elif msg.reason == ofp.OFPPR_MODIFY:
            reason = 'MODIFY'
        else:
            reason = 'unknown'

        self.logger.info('OFPPortStatus received at: : reason=%s desc=%s', reason, msg.desc)
        #print "O msg.desc e: ", msg.desc.port_no, msg.desc.name, msg.desc.state

        if self.switches_in_topology.has_key(dp.id):
            for i in range(len(self.switches_in_topology[dp.id])):
                try:
                    if msg.desc.port_no == self.switches_in_topology[dp.id][i]['port_no'] and reason == 'MODIFY':
                        self.switches_in_topology[dp.id][i]['hw_addr'] = msg.desc.hw_addr
                        self.switches_in_topology[dp.id][i]['name'] = msg.desc.name
                        if msg.desc.state == 0:
                            state = "ADMIN_UP"

                        elif msg.desc.state == 1:
                            state = "ADMIN_DOWN"
                        self.switches_in_topology[dp.id][i]['admin_state'] = state

                    if msg.desc.port_no == self.switches_in_topology[dp.id][i]['port_no'] and reason == 'DELETE':
                        del self.switches_in_topology[dp.id][i]

                except Exception:
                    pass

            if reason == 'ADD':
                if msg.desc.state == 0:
                    state = "ADMIN_UP"

                elif msg.desc.state == 1:
                    state = "ADMIN_DOWN"
                #self.switches_in_topology[dp.id].append(switch_port(msg.desc.port_no, msg.desc.hw_addr, msg.desc.name, state))
                self.switches_in_topology[dp.id].append({'port_no': msg.desc.port_no, 'hw_addr': msg.desc.hw_addr, 'name': msg.desc.name, 'admin_state': state, 'link_state': None})
        self.logger.info('Global Dictionary {DPID: [sw_desc, <ports>]} for DPID: %s has been UPDATED: %s', str(dp.id), str(self.switches_in_topology))
        file_interfaces = open(self.interfaces_info, 'w')
        json.dump(self.switches_in_topology, file_interfaces, sort_keys=True, indent=4, separators=(',',': '))
        file_interfaces.close()

        if reason == 'MODIFY' or reason == 'DELETE':
            if self.dpid_match_actions_dic.has_key(dp.id):
                self.logger.debug('DPID match actions dictionary for DPID: %s is: %s.', str(dp.id), str(self.dpid_match_actions_dic[dp.id]))

                for i in range(len(self.dpid_match_actions_dic[dp.id])):
                    self.logger.debug('Object of the match actions dictionary is: %s.', str(self.dpid_match_actions_dic[dp.id][i]))
                    self.logger.debug('port_status_handler: Match of the object is: %s.', str(self.dpid_match_actions_dic[dp.id][i]['match']))
                    if str(self.dpid_match_actions_dic[dp.id][i]['match']['in_port']) == str(msg.desc.port_no):
                        self.logger.info('Match and Actions Dictionary for DPID: %s has a match for port: %s. Deleting flow.', str(dp.id), str(msg.desc.port_no))
                        match = self.dpid_match_actions_dic[dp.id][i]['match']
                        actions = self.dpid_match_actions_dic[dp.id][i]['actions']
                        self.mod_flow(dp, 1, match, actions, ofp.OFPFC_DELETE)
        return

    def send_echo_request(self, datapath, data="aG9nZQ=="):
        ofp_parser = datapath.ofproto_parser

        req = ofp_parser.OFPEchoRequest(datapath, data)
        datapath.send_msg(req)
        self.logger.debug('OFPEchoRequest has been sent to: %s', str(datapath.id))
        return

	
    @set_ev_cls(ofp_event.EventOFPEchoReply,
                [HANDSHAKE_DISPATCHER, CONFIG_DISPATCHER, MAIN_DISPATCHER])
    def echo_reply_handler(self, ev):
        self.logger.debug('OFPEchoReply received: data=%s',
                          hex_array(ev.msg.data))
        return
