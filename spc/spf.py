import logging

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
