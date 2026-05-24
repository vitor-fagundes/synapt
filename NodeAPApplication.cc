#include "NodeAPApplication.h"
#include "NodeApplication.h"
#include "ns3/address.h"
#include "ns3/ipv6.h"
#include "ns3/udp-socket-factory.h"
#include "MyTag.h"
#include "constants.h"

#include <iostream>
#include <algorithm>
#include <random>
#include "ns3/lr-wpan-net-device.h"
#include "ns3/lr-wpan-spectrum-value-helper.h"
#include "ns3/spectrum-value.h"
#include "ns3/mobility-model.h"

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("Contaski_V1_AP");

namespace nr2{
    // Limiar de similaridade para REALOCAÇÃO em cluster existente (RL1)
    // Mais flexível que o threshold de formação (0.95), pois órfãos tentam
    // entrar em clusters formados por outros nós — similaridade natural é menor
    const double REALLOCATION_THRESHOLD = 0.85;

    // Limiar de similaridade para RECLUSTERIZAÇÃO entre órfãos (RL2)
    // Mesmo valor da formação original dos clusters (NodeApplication.cc)
    const double CLUSTERING_THRESHOLD = 0.85;

    Ipv6Address NodeAPApplication::GetNodeIpAddress(){
        Ptr <Node> PtrNode = this->GetNode();
        Ptr<Ipv6> ipv6 = PtrNode->GetObject<Ipv6> ();
        Ipv6InterfaceAddress iaddr = ipv6->GetAddress (1,0);
        Ipv6Address ipAddr = iaddr.GetAddress();

        return ipAddr;
    }

    void NodeAPApplication::setup(){
        this->m_node = GetNodeIpAddress();
        this->m_tid = ns3::UdpSocketFactory::GetTypeId();
        this->m_socket = this->GetNode()->GetObject<Socket>();
        this->clusterLeaders = new std::vector<Ipv6Address>;
        this->aptLeaders = new std::vector<Ipv6Address>;
        this->dispatchedTasks = new taskVector();

        this->confirmationsSinceLastDispatch = 0;
        this->failurePercentage = 0.0;
        this->failurePercentageMin = 0.0;
        this->failurePercentageMax = 0.0;
        this->failureTimeMin = 310.0;  // Default: 310s
        this->failureTimeMax = 310.0;  // Default: 310s (tempo fixo)
        this->actualFailureTime = 0.0;
        this->actualFailurePercentage = 0.0;

        this->totalNetworkNodes = 0;
        this->decisionInterval = 60.0;  // Ciclo de decisão a cada 60 segundos
        this->allNodesRegistered = false;

        // Cenário 4: Múltiplas falhas sequenciais
        this->multipleFailuresEnabled = false;
        this->multipleFailurePercentage = 0.0;
        this->currentFailureWave = 0;

        // v2: default é v1 (falha de líder); contaski.cc seta "member" para v2
        this->failureMode = "leader";

        // Inicializar módulos do aprendizado intuitivo
        this->intuitiveEngine = new IntuitiveLearningEngine();
        this->anomalyDetector = new AnomalyDetector(2.0, 20);  // Z-threshold=2.0, janela=20

        for(auto task:*this->tasks){
            task->print();
        }
    }

    void NodeAPApplication::StartApplication(){
        // Override combinado por densidade da rede para N>=300. Ver IntuitiveLearning.h
        // (XI_ANOMALOUS_300, THRESHOLD_S1_300) para a justificativa detalhada:
        //   (i)  ξ_anomalous=-0.033 reduz o drag absoluto sobre mean(Li) (paper-citado)
        //   (ii) THRESHOLD_S1=0.245 recalibra o gatilho S1/S2 (hyperparâmetro interno)
        if(this->totalNetworkNodes >= 300){
            this->intuitiveEngine->getDistributedLearning().setXiAnomalous(XI_ANOMALOUS_300);
            this->intuitiveEngine->getDualSystemMut().setThresholdS1(THRESHOLD_S1_300);
            NS_LOG_INFO("DENSITY_OVERRIDE: N=" << this->totalNetworkNodes
                        << " → ξ_anomalous=" << XI_ANOMALOUS_300
                        << ", THRESHOLD_S1=" << THRESHOLD_S1_300);
        }

        // If socket is not created yet
        if(!this->m_socket){
            // Create socket
            auto netdev = this->GetNode()->GetDevice(2);
            
            this->m_socket = Socket::CreateSocket(GetNode(), m_tid);
            this->m_socket->BindToNetDevice(netdev);
            
            this->m_socket->SetAllowBroadcast(true);
            this->m_socket->Bind(Inet6SocketAddress(Ipv6Address::GetAny (), 2020));
            this->m_socket->Listen();
            this->m_socket->SetRecvCallback(MakeCallback (&NodeAPApplication::recvCallback, this));
        }

        m_socket->SetRecvCallback(MakeCallback (&NodeAPApplication::recvCallback, this));
        Simulator::Schedule(Seconds(150), &NodeAPApplication::sendTaskToLeaders, this);

        // Carregar conhecimento persistente de rodadas anteriores (se disponível)
        if(!this->knowledgePath.empty()){
            this->intuitiveEngine->loadKnowledge(this->knowledgePath);
        }

        // Calcular tempo e porcentagem de falha (pode ser fixo ou aleatório)
        // Usa RNG do NS-3 para reprodutibilidade via seed+run
        Ptr<UniformRandomVariable> failRng = CreateObject<UniformRandomVariable>();

        // Determinar tempo de falha
        if(this->failureTimeMin < this->failureTimeMax){
            // Tempo aleatório entre min e max
            failRng->SetAttribute("Min", DoubleValue(this->failureTimeMin));
            failRng->SetAttribute("Max", DoubleValue(this->failureTimeMax));
            this->actualFailureTime = failRng->GetValue();
        } else {
            // Tempo fixo
            this->actualFailureTime = this->failureTimeMin;
        }

        // Determinar porcentagem de falha
        if(this->failurePercentageMin > 0 && this->failurePercentageMin < this->failurePercentageMax){
            // Porcentagem aleatória entre min e max
            failRng->SetAttribute("Min", DoubleValue(this->failurePercentageMin));
            failRng->SetAttribute("Max", DoubleValue(this->failurePercentageMax));
            this->actualFailurePercentage = failRng->GetValue();
        } else if(this->failurePercentage > 0){
            // Porcentagem fixa
            this->actualFailurePercentage = this->failurePercentage;
        } else {
            this->actualFailurePercentage = 0.0;
        }

        // Agendar falhas se houver porcentagem configurada
        if(this->multipleFailuresEnabled){
            // v2: rotear para wave de membros ou de líderes conforme failureMode
            bool memberMode = (this->failureMode == "member");
            NS_LOG_INFO("FAILURE_CONFIG: Modo múltiplas falhas ativado [" << this->failureMode
                        << "] — " << this->failureTimes.size() << " ondas, "
                        << this->multipleFailurePercentage << "% cada");
            for(size_t i = 0; i < this->failureTimes.size(); i++){
                NS_LOG_INFO("FAILURE_CONFIG: Onda " << (i+1) << " agendada para t=" << this->failureTimes[i] << "s");
                if(memberMode){
                    Simulator::Schedule(Seconds(this->failureTimes[i]),
                                        &NodeAPApplication::triggerMemberFailureWave, this);
                } else {
                    Simulator::Schedule(Seconds(this->failureTimes[i]),
                                        &NodeAPApplication::triggerMultipleFailureWave, this);
                }
            }
        }
        else if(this->actualFailurePercentage > 0.0){
            NS_LOG_INFO("FAILURE_CONFIG: Tempo=" << this->actualFailureTime << "s, Porcentagem=" << this->actualFailurePercentage << "%");
            Simulator::Schedule(Seconds(this->actualFailureTime), &NodeAPApplication::triggerLeaderFailures, this);
        }

        // ============================================================
        // Agendar primeiro ciclo de decisão do aprendizado intuitivo
        // Líderes registram em t=105s; ciclo começa em t=115s (antes do primeiro dispatch em t=150s)
        Simulator::Schedule(Seconds(160.0), &NodeAPApplication::intuitiveDecisionCycle, this);
    }

    void NodeAPApplication::StopApplication(){
        this->m_socket->Close();

        stringstream out;
        for(auto task: *this->dispatchedTasks){
            out << task->serialize() << "\n";
        }

        out << "\n\n";

        for(auto task: *this->tasks){
            out << task->serialize() << "\n";
        }

        ofstream outFile("APStats.txt");
        outFile << out.str();
        outFile.close();

        // Escrever log do aprendizado intuitivo
        writeIntuitiveLog();

        // Salvar conhecimento persistente para próxima rodada
        if(!this->knowledgePath.empty()){
            this->intuitiveEngine->saveKnowledge(this->knowledgePath);
        }
    }

    TypeId NodeAPApplication::GetTypeId(){
        static TypeId tid = TypeId ("ns3::NodeAPApplication")
            .SetParent<Application>()
            .SetGroupName("Applications")
            .AddConstructor<NodeAPApplication>()
            .AddAttribute ("Protocol", "The type of protocol to use. This should be "
                   "a subclass of ns3::SocketFactory",
                   TypeIdValue (UdpSocketFactory::GetTypeId ()),
                   MakeTypeIdAccessor (&NodeAPApplication::m_tid),
                   // This should check for SocketFactory as a parent
                   MakeTypeIdChecker ())
            ;

        return tid;
    }

    void NodeAPApplication::generateTasks(int totalDuration){
        if(this->tasks == nullptr){
            this->tasks = new taskVector();
        }else{
            this->tasks->clear();
        }

        for(int i = 0; i < 10; i++){
            this->tasks->push_back(new Task());
        }
    }

    void NodeAPApplication::sendTaskToLeaders(){
        // Select one task
        this->currentDispatchedTask = this->tasks->front();

        if(!this->currentDispatchedTask)
            return;

        this->tasks->pop_front();
        std::string serializedTask = this->currentDispatchedTask->serialize();

        // Guardar o quorum necessário (número de agrupamentos)
        this->requiredQuorum = this->currentDispatchedTask->getQuorum();

        // Dispatch
        uint16_t port = 2020;
        /*
        Inet6SocketAddress remote = Inet6SocketAddress(Ipv6Address("FF02::1"), port);
        status = this->m_socket->Connect(remote);
        if(status == -1){
            NS_LOG_INFO("Could not bind socket");
        }*/


        Ptr<Packet> pack = Create<Packet>(reinterpret_cast<const uint8_t*> (serializedTask.c_str()), serializedTask.size());
        MyTag tag;
        tag.SetSimpleValue(MessageTypes::TaskDispatch);
        pack->AddPacketTag(tag);

        for(size_t i = 0; i < clusterLeaders->size(); i++) {
            Inet6SocketAddress remote = Inet6SocketAddress(clusterLeaders->at(i), port);
            int status = this->m_socket->SendTo(pack, 0, remote);
            
            if(status == -1){
                NS_LOG_FUNCTION("Could not dispatch task to " << Address(remote.GetIpv6()) << " at port "+remote.GetPort());
            }
            NS_LOG_INFO("AP: MS (" << this->GetNodeIpAddress() << ", " << remote.GetIpv6() << ", " << status << ")");
            // Contabilizar dispatch para taxa de aceitação
            taskDispatchCount[clusterLeaders->at(i)]++;
        }

        this->confirmationsSinceLastDispatch = 0;

        NS_LOG_INFO("AP: TD " << this->currentDispatchedTask->getTid() << " " << Simulator::Now().GetSeconds());
        Simulator::Schedule(Seconds(10), &NodeAPApplication::taskConfirmation, this);
    }

    void NodeAPApplication::recvCallback(Ptr<Socket> socket){
        Ptr<Packet> packet;
        Address from;
        Ipv6Address fromIP;
        MyTag tag;
        
        while((packet = socket->RecvFrom(from))){
            fromIP = Inet6SocketAddress::ConvertFrom(from).GetIpv6();
            packet->PeekPacketTag(tag);

            switch (tag.GetSimpleValue()){
                case MessageTypes::LeaderRegister:
                {
                    NS_LOG_INFO("LR: " << fromIP << " at " << Simulator::Now().GetSeconds());
                    this->clusterLeaders->push_back(fromIP);

                    // Registrar líder no motor intuitivo
                    // Li inicial = energia aleatória em [0.5, 1.0] como no Python:
                    //   self.L = {v: G.nodes[v].get("energy", 0.5)} com energy ~ U(0.5, 1.0)
                    {
                        Ptr<UniformRandomVariable> liRng = CreateObject<UniformRandomVariable>();
                        liRng->SetAttribute("Min", DoubleValue(0.5));
                        liRng->SetAttribute("Max", DoubleValue(1.0));
                        double initialLi = liRng->GetValue();
                        this->intuitiveEngine->registerNode(fromIP, initialLi, true);
                    }

                    // Inicializar informação do cluster
                    ClusterInfo info;
                    info.leaderAddr = fromIP;
                    info.leaderAlive = true;
                    info.memberCount = 0;
                    info.initialMemberCount = 0;
                    info.lastHeartbeat = Simulator::Now().GetSeconds();
                    info.hasAcceptedTask = false;
                    clusterInfoMap[fromIP] = info;
                    break;
                }
                
                case MessageTypes::TaskAccept:
                    NS_LOG_INFO("AP: TA " << this->currentDispatchedTask->getTid() << ", " << fromIP << " " << Simulator::Now().GetSeconds());
                    this->confirmationsSinceLastDispatch++;
                    // Registrar líder como apto
                    if(std::find(this->aptLeaders->begin(), this->aptLeaders->end(), fromIP) == this->aptLeaders->end()){
                        this->aptLeaders->push_back(fromIP);
                            // Atualizar informação do cluster
                        auto it = clusterInfoMap.find(fromIP);
                        if(it != clusterInfoMap.end()){
                            it->second.lastHeartbeat = Simulator::Now().GetSeconds();
                            it->second.hasAcceptedTask = true;
                        }

                        // Contabilizar aceitação
                        taskAcceptCount[fromIP]++;
                    }
                    break;
                case MessageTypes::HeartbeatReport:
                {
                    // Heartbeat confirma que o líder está vivo
                    // memberCount é calculado via myLeader no intuitiveDecisionCycle
                    auto it = clusterInfoMap.find(fromIP);
                    if(it != clusterInfoMap.end()){
                        it->second.lastHeartbeat = Simulator::Now().GetSeconds();
                    }
                    break;
                }

                default:
                    break;
            }
        }
    }

    void NodeAPApplication::taskConfirmation(){
        // If no confimation: Re-enqeue the task
        if(this->confirmationsSinceLastDispatch < this->requiredQuorum){
            // Não atingiu o quorum mínimo de agrupamentos - re-enfileirar
            NS_LOG_INFO("AP: Task " << this->currentDispatchedTask->getTid() 
                        << " failed quorum check: " << this->confirmationsSinceLastDispatch 
                        << "/" << this->requiredQuorum << " clusters accepted");
            this->tasks->push_back(this->currentDispatchedTask);
            Simulator::Schedule(Seconds(1), &NodeAPApplication::sendTaskToLeaders, this);
        }
        else{
            // Quorum atingido - tarefa aceita por agrupamentos suficientes
            NS_LOG_INFO("AP: Task " << this->currentDispatchedTask->getTid() 
                        << " quorum satisfied: " << this->confirmationsSinceLastDispatch 
                        << "/" << this->requiredQuorum << " clusters accepted");
            // Wait for task to be completed
            auto current = this->currentDispatchedTask;
            this->dispatchedTasks->push_back(current);

            // Dispatch another task
            Simulator::Schedule(Seconds(this->currentDispatchedTask->getDuration()), &NodeAPApplication::sendTaskToLeaders, this);
        }
    }

    void NodeAPApplication::setTasks(taskVector* tasks){
        this->tasks = tasks;
    }

    void NodeAPApplication::setFailurePercentage(double percentage){
        this->failurePercentage = percentage;
    }

    void NodeAPApplication::setFailurePercentageRange(double min, double max){
        this->failurePercentageMin = min;
        this->failurePercentageMax = max;
    }

    void NodeAPApplication::setFailureTimeRange(double min, double max){
        this->failureTimeMin = min;
        this->failureTimeMax = max;
    }

    void NodeAPApplication::setNodes(NodeContainer nodes){
        this->networkNodes = nodes;
    }

    // ============================================================
    // Helper: encontrar Ptr<Node> pelo endereço IPv6
    // ============================================================
    static Ptr<Node> findNodeByAddress(NodeContainer& nodes, Ipv6Address addr){
        for(uint32_t j = 0; j < nodes.GetN(); j++){
            Ptr<Node> node = nodes.Get(j);
            Ptr<Ipv6> ipv6 = node->GetObject<Ipv6>();
            if(ipv6->GetAddress(1, 0).GetAddress() == addr){
                return node;
            }
        }
        return nullptr;
    }

    void NodeAPApplication::setMultipleFailures(std::vector<double> times, double percentage){
        this->multipleFailuresEnabled = true;
        this->failureTimes = times;
        this->multipleFailurePercentage = percentage;
        this->multipleFailurePercentages.clear();   // modo % fixa: garante fallback ao scalar
        this->currentFailureWave = 0;
    }

    void NodeAPApplication::setRandomFailures(int nWaves, double tMin, double tMax,
                                              double pMin, double pMax){
        if(nWaves <= 0) return;

        // Usa std::random_device (entropia do SO) em vez do RNG do NS-3 para garantir
        // sorteio diferente a cada execução mesmo com --run=0 fixo. Mesmo padrão
        // de generateCap em contaski.cc.
        std::random_device rd;
        std::default_random_engine gen{rd()};
        std::uniform_real_distribution<double> timeDist(tMin, tMax);
        std::uniform_real_distribution<double> pctDist(pMin, pMax);

        std::vector<double> times;
        times.reserve(nWaves);
        for(int i = 0; i < nWaves; i++) times.push_back(timeDist(gen));
        std::sort(times.begin(), times.end());      // ordena ascendente; pode haver sobreposição

        std::vector<double> pcts;
        pcts.reserve(nWaves);
        for(int i = 0; i < nWaves; i++) pcts.push_back(pctDist(gen));

        this->multipleFailuresEnabled = true;
        this->failureTimes = times;
        this->multipleFailurePercentages = pcts;
        this->multipleFailurePercentage = 0.0;
        this->currentFailureWave = 0;

        for(int i = 0; i < nWaves; i++){
            NS_LOG_INFO("FAILURE_CONFIG_RANDOM: Onda " << (i+1)
                        << " t=" << times[i] << "s, "
                        << pcts[i] << "%");
        }
    }

    void NodeAPApplication::triggerMultipleFailureWave(){
        this->currentFailureWave++;
        double now = Simulator::Now().GetSeconds();

        // % desta onda: vetor por onda (full random) tem prioridade; fallback para scalar (cenário 4)
        double waveFailurePercentage = this->multipleFailurePercentage;
        if(!this->multipleFailurePercentages.empty()){
            size_t idx = (size_t)(this->currentFailureWave - 1);
            if(idx < this->multipleFailurePercentages.size()){
                waveFailurePercentage = this->multipleFailurePercentages[idx];
            }
        }

        NS_LOG_INFO("FAILURE_WAVE: === Onda " << this->currentFailureWave
                    << " de " << this->failureTimes.size()
                    << " no tempo " << now << "s ===");

        // Recalcular líderes aptos AGORA (vivos + com cluster real)
        // Diferente do cenário original que usa aptLeaders acumulados desde o início,
        // aqui precisamos verificar quem está VIVO e ATIVO neste momento
        std::vector<Ipv6Address> currentEligibleLeaders;

        for(const auto& entry : clusterInfoMap){
            if(!entry.second.leaderAlive) continue;

            Ipv6Address leaderAddr = entry.first;
            Ptr<Node> leaderNode = findNodeByAddress(this->networkNodes, leaderAddr);
            if(!leaderNode) continue;
            
            Ptr<NodeApplication> leaderApp = DynamicCast<NodeApplication>(
                leaderNode->GetApplication(0));
            if(!leaderApp || !leaderApp->isNodeAlive()) continue;

            // Contar membros reais: nós vivos cujo myLeader é este líder (excluindo o próprio)
            int realMembers = 0;
            for(uint32_t j = 0; j < this->networkNodes.GetN(); j++){
                Ptr<Node> n = this->networkNodes.Get(j);
                Ptr<NodeApplication> nApp = DynamicCast<NodeApplication>(
                    n->GetApplication(0));
                if(!nApp || !nApp->isNodeAlive()) continue;
                if(nApp->getMyLeader() == leaderAddr && 
                   nApp->GetNodeIpAddress() != leaderAddr){
                    realMembers++;
                }
            }

            if(realMembers > 0){
                currentEligibleLeaders.push_back(leaderAddr);
                NS_LOG_INFO("FAILURE_WAVE_ELIGIBLE: Leader " << leaderAddr 
                            << " with " << realMembers << " real members"
                            << " (clusterList size=" << leaderApp->getClusterSize() << ")");
            } else {
                NS_LOG_INFO("FAILURE_WAVE_SKIPPED: Leader " << leaderAddr 
                            << " has 0 real members (too small)");
            }
        }

        if(currentEligibleLeaders.empty()){
            NS_LOG_INFO("FAILURE_WAVE: Nenhum líder elegível na onda " 
                        << this->currentFailureWave);
            return;
        }

        // Calcular quantos líderes irão falhar (% da onda atual sobre os elegíveis AGORA)
        int totalEligible = currentEligibleLeaders.size();
        int leadersToFail = (int)std::ceil(totalEligible * waveFailurePercentage / 100.0);

        if(leadersToFail == 0 && waveFailurePercentage > 0){
            leadersToFail = 1;
        }

        NS_LOG_INFO("FAILURE_WAVE: " << leadersToFail << " de " << totalEligible
                    << " líderes elegíveis irão falhar ("
                    << waveFailurePercentage << "%)");

        // Embaralhar para seleção aleatória
        Ptr<UniformRandomVariable> shuffleRng = CreateObject<UniformRandomVariable>();
        std::vector<Ipv6Address> shuffled = currentEligibleLeaders;
        // Fisher-Yates usando RNG do NS-3 (determinístico via seed+run)
        for(int i = shuffled.size() - 1; i > 0; i--){
            shuffleRng->SetAttribute("Min", DoubleValue(0));
            shuffleRng->SetAttribute("Max", DoubleValue(i + 0.999));
            int j = (int)shuffleRng->GetValue();
            if(j > i) j = i;
            std::swap(shuffled[i], shuffled[j]);
        }

        // Aplicar falhas
        int totalRealOrphans = 0;
        for(int i = 0; i < leadersToFail; i++){
            Ipv6Address leaderToFail = shuffled[i];

            Ptr<Node> node = findNodeByAddress(this->networkNodes, leaderToFail);
            if(!node) continue;

            Ptr<Application> app = node->GetApplication(0);
            if(!app) continue;

            Ptr<NodeApplication> nodeApp = DynamicCast<NodeApplication>(app);
            if(!nodeApp) continue;

            // Contar órfãos reais: nós vivos cujo myLeader é este líder (excluindo o próprio)
            int realOrphans = 0;
            for(uint32_t j = 0; j < this->networkNodes.GetN(); j++){
                Ptr<Node> n = this->networkNodes.Get(j);
                Ptr<NodeApplication> nApp = DynamicCast<NodeApplication>(n->GetApplication(0));
                if(!nApp || !nApp->isNodeAlive()) continue;
                if(nApp->getMyLeader() == leaderToFail && 
                   nApp->GetNodeIpAddress() != leaderToFail){
                    realOrphans++;
                }
            }
            totalRealOrphans += realOrphans;

            NS_LOG_INFO("FAILURE_WAVE: Líder " << leaderToFail 
                        << " falhou na onda " << this->currentFailureWave
                        << " no tempo " << now << " - " << realOrphans << " nós órfãos"
                        << " (clusterList size=" << nodeApp->getClusterSize() << ")");
            
            // Parar a aplicação do nó
            Simulator::ScheduleNow(&Application::SetStopTime, app, Simulator::Now());
            nodeApp->StopApplication();

            // Notificar motor intuitivo sobre a morte do líder
            intuitiveEngine->registerLeaderDeath(leaderToFail);

            // Atualizar mapa de clusters
            auto it = clusterInfoMap.find(leaderToFail);
            if(it != clusterInfoMap.end()){
                it->second.leaderAlive = false;
            }
        }

        NS_LOG_INFO("FAILURE_WAVE: Onda " << this->currentFailureWave
                    << " completa — " << leadersToFail << " líderes falharam"
                    << ", " << totalRealOrphans << " órfãos reais");
    }

    // ============================================================
    // v2: Onda de falha em nós-membro (Estratégia A — pool global)
    // ============================================================
    void NodeAPApplication::triggerMemberFailureWave(){
        this->currentFailureWave++;
        double now = Simulator::Now().GetSeconds();

        double waveFailurePercentage = this->multipleFailurePercentage;
        if(!this->multipleFailurePercentages.empty()){
            size_t idx = (size_t)(this->currentFailureWave - 1);
            if(idx < this->multipleFailurePercentages.size()){
                waveFailurePercentage = this->multipleFailurePercentages[idx];
            }
        }

        NS_LOG_INFO("MEMBER_FAILURE_WAVE: === Onda " << this->currentFailureWave
                    << " de " << this->failureTimes.size()
                    << " no tempo " << now << "s ===");

        // Construir pool global de membros vivos (não-líderes, com myLeader vivo)
        // Líder vivo é pré-requisito para garantir que o nó é um MEMBRO, não órfão pré-existente
        std::set<Ipv6Address> aliveLeaders;
        for(const auto& entry : clusterInfoMap){
            if(!entry.second.leaderAlive) continue;
            Ptr<Node> leaderNode = findNodeByAddress(this->networkNodes, entry.first);
            if(!leaderNode) continue;
            Ptr<NodeApplication> leaderApp = DynamicCast<NodeApplication>(
                leaderNode->GetApplication(0));
            if(leaderApp && leaderApp->isNodeAlive()){
                aliveLeaders.insert(entry.first);
            }
        }

        std::vector<Ipv6Address> memberPool;
        for(uint32_t j = 0; j < this->networkNodes.GetN(); j++){
            Ptr<Node> n = this->networkNodes.Get(j);
            Ptr<NodeApplication> nApp = DynamicCast<NodeApplication>(n->GetApplication(0));
            if(!nApp || !nApp->isNodeAlive()) continue;

            Ipv6Address addr = nApp->GetNodeIpAddress();
            // Excluir líderes do pool
            if(aliveLeaders.count(addr) > 0) continue;
            // Excluir órfãos (myLeader morto ou inválido)
            Ipv6Address ldr = nApp->getMyLeader();
            if(aliveLeaders.count(ldr) == 0) continue;
            // Não pode ser membro de si mesmo (já excluído acima, redundante)
            if(ldr == addr) continue;

            memberPool.push_back(addr);
        }

        if(memberPool.empty()){
            NS_LOG_INFO("MEMBER_FAILURE_WAVE: Pool de membros vazio na onda "
                        << this->currentFailureWave);
            return;
        }

        int totalMembers = memberPool.size();
        int membersToFail = (int)std::ceil(totalMembers * waveFailurePercentage / 100.0);
        if(membersToFail == 0 && waveFailurePercentage > 0) membersToFail = 1;

        NS_LOG_INFO("MEMBER_FAILURE_WAVE: " << membersToFail << " de " << totalMembers
                    << " membros vivos irão falhar (" << waveFailurePercentage << "%)");

        // Fisher-Yates determinístico via RNG do NS-3
        Ptr<UniformRandomVariable> shuffleRng = CreateObject<UniformRandomVariable>();
        for(int i = (int)memberPool.size() - 1; i > 0; i--){
            shuffleRng->SetAttribute("Min", DoubleValue(0));
            shuffleRng->SetAttribute("Max", DoubleValue(i + 0.999));
            int j = (int)shuffleRng->GetValue();
            if(j > i) j = i;
            std::swap(memberPool[i], memberPool[j]);
        }

        std::map<Ipv6Address, int> deathsPerLeader;
        for(int i = 0; i < membersToFail; i++){
            Ipv6Address memberAddr = memberPool[i];
            Ptr<Node> node = findNodeByAddress(this->networkNodes, memberAddr);
            if(!node) continue;
            Ptr<NodeApplication> mApp = DynamicCast<NodeApplication>(node->GetApplication(0));
            if(!mApp) continue;

            Ipv6Address leaderOfMember = mApp->getMyLeader();
            deathsPerLeader[leaderOfMember]++;

            NS_LOG_INFO("MEMBER_FAILURE_WAVE: Membro " << memberAddr
                        << " (cluster=" << leaderOfMember << ") falhou no tempo " << now);

            // Parar a aplicação do membro
            Ptr<Application> app = node->GetApplication(0);
            Simulator::ScheduleNow(&Application::SetStopTime, app, Simulator::Now());
            mApp->StopApplication();
        }

        // Log resumo por cluster impactado
        for(const auto& entry : deathsPerLeader){
            NS_LOG_INFO("MEMBER_FAILURE_WAVE: Cluster " << entry.first
                        << " perdeu " << entry.second << " membro(s) nesta onda");
        }

        NS_LOG_INFO("MEMBER_FAILURE_WAVE: Onda " << this->currentFailureWave
                    << " completa — " << membersToFail << " membros falharam em "
                    << deathsPerLeader.size() << " cluster(s)");
    }

void NodeAPApplication::triggerLeaderFailures(){
        if(this->aptLeaders->empty()){
            NS_LOG_INFO("FAILURE: Nenhum líder apto para aplicar falha no tempo " << Simulator::Now().GetSeconds());
            return;
        }

        // Filtrar líderes aptos que tenham membros reais (via myLeader)
        std::vector<Ipv6Address> eligibleLeaders;
        for(auto& leaderAddr : *this->aptLeaders){
            Ptr<Node> leaderNode = findNodeByAddress(this->networkNodes, leaderAddr);
            if(!leaderNode) continue;
            Ptr<NodeApplication> leaderApp = DynamicCast<NodeApplication>(
                leaderNode->GetApplication(0));
            if(!leaderApp || !leaderApp->isNodeAlive()) continue;

            // Contar membros reais: nós vivos cujo myLeader é este líder (excluindo o próprio)
            int realMembers = 0;
            for(uint32_t j = 0; j < this->networkNodes.GetN(); j++){
                Ptr<Node> node = this->networkNodes.Get(j);
                Ptr<NodeApplication> nApp = DynamicCast<NodeApplication>(
                    node->GetApplication(0));
                if(!nApp || !nApp->isNodeAlive()) continue;
                if(nApp->getMyLeader() == leaderAddr && 
                   nApp->GetNodeIpAddress() != leaderAddr){
                    realMembers++;
                }
            }

            if(realMembers > 0){
                eligibleLeaders.push_back(leaderAddr);
                NS_LOG_INFO("FAILURE_ELIGIBLE: Leader " << leaderAddr 
                            << " with " << realMembers << " real members"
                            << " (clusterList size=" << leaderApp->getClusterSize() << ")");
            } else {
                NS_LOG_INFO("FAILURE_SKIPPED: Leader " << leaderAddr 
                            << " has 0 real members (too small)");
            }
        }

        if(eligibleLeaders.empty()){
            NS_LOG_INFO("FAILURE: Nenhum líder elegível (todos os clusters têm apenas 1 nó) no tempo " << Simulator::Now().GetSeconds());
            return;
        }

        // Calcular quantos líderes irão falhar baseado nos ELEGÍVEIS
        int totalEligibleLeaders = eligibleLeaders.size();
        int leadersToFail = (int)std::ceil(totalEligibleLeaders * this->actualFailurePercentage / 100.0);
        
        // Se a porcentagem > 0 mas o cálculo deu 0, forçar pelo menos 1
        if(leadersToFail == 0 && this->actualFailurePercentage > 0){
            leadersToFail = 1;
        }

        NS_LOG_INFO("FAILURE: " << leadersToFail << " de " << totalEligibleLeaders << " líderes elegíveis irão falhar (" << this->actualFailurePercentage << "%)");
        NS_LOG_INFO("FAILURE: (Total aptos: " << this->aptLeaders->size() << ", Elegíveis após filtro: " << totalEligibleLeaders << ")");

        // Embaralhar lista de líderes ELEGÍVEIS para seleção aleatória
        // Fisher-Yates usando RNG do NS-3 (determinístico via seed+run)
        Ptr<UniformRandomVariable> shuffleRng = CreateObject<UniformRandomVariable>();
        for(int i = eligibleLeaders.size() - 1; i > 0; i--){
            shuffleRng->SetAttribute("Min", DoubleValue(0));
            shuffleRng->SetAttribute("Max", DoubleValue(i + 0.999));
            int j = (int)shuffleRng->GetValue();
            if(j > i) j = i;
            std::swap(eligibleLeaders[i], eligibleLeaders[j]);
        }

        // Aplicar falha nos líderes selecionados
        for(int i = 0; i < leadersToFail; i++){
            Ipv6Address leaderToFail = eligibleLeaders[i];

            // Encontrar o nó correspondente e desligá-lo
            for(uint32_t j = 0; j < this->networkNodes.GetN(); j++){
                Ptr<Node> node = this->networkNodes.Get(j);
                Ptr<Ipv6> ipv6 = node->GetObject<Ipv6>();
                Ipv6Address nodeAddr = ipv6->GetAddress(1, 0).GetAddress();

                if(nodeAddr == leaderToFail){
                    // Parar a aplicação do nó (falha completa)
                    Ptr<Application> app = node->GetApplication(0);
                    if(app){
                        Simulator::ScheduleNow(&Application::SetStopTime, app, Simulator::Now());
                        Ptr<NodeApplication> nodeApp = DynamicCast<NodeApplication>(app);
                        if(nodeApp){
                            // Contar órfãos reais: nós vivos cujo myLeader é este líder
                            int realOrphans = 0;
                            for(uint32_t k = 0; k < this->networkNodes.GetN(); k++){
                                Ptr<Node> n = this->networkNodes.Get(k);
                                Ptr<NodeApplication> nApp = DynamicCast<NodeApplication>(
                                    n->GetApplication(0));
                                if(!nApp || !nApp->isNodeAlive()) continue;
                                if(nApp->getMyLeader() == leaderToFail && 
                                   nApp->GetNodeIpAddress() != leaderToFail){
                                    realOrphans++;
                                }
                            }
                            NS_LOG_INFO("FAILURE: Líder " << leaderToFail << " falhou no tempo " 
                                        << Simulator::Now().GetSeconds() << " - " << realOrphans 
                                        << " nós órfãos (clusterList size=" << nodeApp->getClusterSize() << ")");
                            nodeApp->StopApplication();

                            // Notificar motor intuitivo sobre a morte do líder
                            intuitiveEngine->registerLeaderDeath(leaderToFail);

                            // Atualizar mapa de clusters
                            auto it = clusterInfoMap.find(leaderToFail);
                            if(it != clusterInfoMap.end()){
                                it->second.leaderAlive = false;
                            }
                        }
                    }
                    break;
                }
            }
        }
    }
// ============================================================
    // Aprendizado Intuitivo — Ciclo de Decisão
    // ============================================================

    void NodeAPApplication::intuitiveDecisionCycle(){
        double now = Simulator::Now().GetSeconds();

        // Guard: não rodar ciclo após SIMTIME — nós já pararam
        if(now >= 895.0) {
            return;
        }

        // [0] Primeiro ciclo: registrar TODOS os nós no aprendizado distribuído
        // No Python: self.L = {v: G.nodes[v].get("energy", 0.5) for v in G.nodes}
        // Inclui sensores, líderes e AP — não apenas líderes
        if(!allNodesRegistered){
            Ptr<UniformRandomVariable> liRng = CreateObject<UniformRandomVariable>();
            liRng->SetAttribute("Min", DoubleValue(0.4));
            liRng->SetAttribute("Max", DoubleValue(1.0));
            
            for(uint32_t j = 0; j < this->networkNodes.GetN(); j++){
                Ptr<Node> node = this->networkNodes.Get(j);
                Ptr<Ipv6> ipv6 = node->GetObject<Ipv6>();
                Ipv6Address nodeAddr = ipv6->GetAddress(1, 0).GetAddress();
                
                // Verificar se já foi registrado como líder
                auto& allNodes = intuitiveEngine->getDistributedLearning().getAllNodes();
                if(allNodes.find(nodeAddr) == allNodes.end()){
                    // Sensor: energia em [0.4, 1.0] como no Python
                    double energy = liRng->GetValue();
                    Ptr<Application> app = node->GetApplication(0);
                    bool isLeader = false;
                    if(app){
                        Ptr<NodeApplication> nodeApp = DynamicCast<NodeApplication>(app);
                        if(nodeApp){
                            isLeader = (clusterInfoMap.find(nodeAddr) != clusterInfoMap.end());
                        }
                    }
                    intuitiveEngine->registerNode(nodeAddr, energy, isLeader);
                }
            }
            allNodesRegistered = true;
            NS_LOG_INFO("INTUITIVE: Registrados " << this->networkNodes.GetN() 
                        << " nós no aprendizado distribuído");
        }

        // [1] Buscar informação atual dos clusters via myLeader (pertencimento real)
        for(auto& entry : clusterInfoMap){
            Ipv6Address leaderAddr = entry.first;

            // Contar membros reais: nós vivos cujo myLeader é este líder (excluindo o próprio)
            int realMembers = 0;
            for(uint32_t j = 0; j < this->networkNodes.GetN(); j++){
                Ptr<Node> node = this->networkNodes.Get(j);
                Ptr<NodeApplication> nodeApp = DynamicCast<NodeApplication>(
                    node->GetApplication(0));
                if(!nodeApp || !nodeApp->isNodeAlive()) continue;
                if(nodeApp->getMyLeader() == leaderAddr && 
                   nodeApp->GetNodeIpAddress() != leaderAddr){
                    realMembers++;
                }
            }
            entry.second.memberCount = realMembers;
            if(entry.second.initialMemberCount == 0 && realMembers > 0){
                entry.second.initialMemberCount = realMembers;
            }

            // Verificar se líder está vivo
            Ptr<Node> leaderNode = findNodeByAddress(this->networkNodes, leaderAddr);
            if(leaderNode){
                Ptr<NodeApplication> leaderApp = DynamicCast<NodeApplication>(
                    leaderNode->GetApplication(0));
                if(leaderApp){
                    entry.second.leaderAlive = leaderApp->isNodeAlive();
                }
            }
        }

        // [2] Atualizar features do detector de anomalias para cada líder
        for(const auto& entry : clusterInfoMap){
            if(!entry.second.leaderAlive) continue;

            NodeFeatures features;
            
            uint32_t dispatched = taskDispatchCount[entry.first];
            uint32_t accepted = taskAcceptCount[entry.first];
            features.taskAcceptRate = (dispatched > 0) ? 
                (double)accepted / dispatched : 1.0;

            features.timeSinceContact = now - entry.second.lastHeartbeat;

            features.clusterHealthRatio = (entry.second.initialMemberCount > 0) ?
                (double)entry.second.memberCount / entry.second.initialMemberCount : 1.0;

            anomalyDetector->updateFeatures(entry.first, features);
        }

        // [3] Executar detecção de anomalias (Módulo 3)
        anomalousNodes = anomalyDetector->detect();

        for(const auto& addr : anomalousNodes){
            auto scores = anomalyDetector->getScores();
            double score = scores.count(addr) > 0 ? scores[addr] : 0.0;
            intuitiveEngine->getKnowledge().addEmergent(addr, now, score);
        }

        // [3.5] Atualizar aprendizado distribuído com mapa de vizinhança real
        {
            std::map<Ipv6Address, std::vector<Ipv6Address>> realClusterMembers;
            for (const auto& entry : clusterInfoMap) {
                if (!entry.second.leaderAlive) continue;
                std::vector<Ipv6Address> members;
                for(uint32_t j = 0; j < this->networkNodes.GetN(); j++){
                    Ptr<Node> node = this->networkNodes.Get(j);
                    Ptr<NodeApplication> nodeApp = DynamicCast<NodeApplication>(
                        node->GetApplication(0));
                    if(!nodeApp || !nodeApp->isNodeAlive()) continue;
                    if(nodeApp->getMyLeader() == entry.first &&
                       nodeApp->GetNodeIpAddress() != entry.first){
                        members.push_back(nodeApp->GetNodeIpAddress());
                    }
                }
                realClusterMembers[entry.first] = members;
            }
            intuitiveEngine->getDistributedLearning().step(realClusterMembers, anomalousNodes);
        }

        // [4] Construir vetor de ClusterInfo e executar ciclo de decisão (métricas globais)
        auto clusters = buildClusterInfoVector();
        IntuitiveSnapshot snap = intuitiveEngine->decisionCycle(
            now, clusters, anomalousNodes, totalNetworkNodes);

        // [5] Processar órfãos com decisão individual por órfão (v1)
        processOrphansIntuitively(snap);

        // [5b] v2: processar clusters degradados (falha de membro)
        processClusterDegradationIntuitively(snap);

        // Persistir snapshot do ciclo (combina contadores de [5] e [5b])
        intuitiveEngine->getHistoryMut().push_back(snap);

        // [5c] v2: ranking de risco por nó (Li ascendente) — inferência subproduto
        dumpRiskRanking(now);

        // [6] Recalcular métricas após ação via myLeader
        for(auto& entry : clusterInfoMap){
            int realMembers = 0;
            for(uint32_t j = 0; j < this->networkNodes.GetN(); j++){
                Ptr<Node> node = this->networkNodes.Get(j);
                Ptr<NodeApplication> nodeApp = DynamicCast<NodeApplication>(
                    node->GetApplication(0));
                if(!nodeApp || !nodeApp->isNodeAlive()) continue;
                if(nodeApp->getMyLeader() == entry.first &&
                   nodeApp->GetNodeIpAddress() != entry.first){
                    realMembers++;
                }
            }
            entry.second.memberCount = realMembers;
            // Após recovery (REALLOCATE/RECLUSTER), atualizar baseline:
            // initialMemberCount representa o melhor estado já observado.
            // Isso permite que reliability volte a 1.0 após recuperação bem-sucedida.
            if(realMembers > (int)entry.second.initialMemberCount){
                entry.second.initialMemberCount = realMembers;
            }

            Ptr<Node> leaderNode = findNodeByAddress(this->networkNodes, entry.first);
            if(leaderNode){
                Ptr<NodeApplication> leaderApp = DynamicCast<NodeApplication>(
                    leaderNode->GetApplication(0));
                if(leaderApp){
                    entry.second.leaderAlive = leaderApp->isNodeAlive();
                }
            }
        }

        auto clustersAfter = buildClusterInfoVector();
        double qiAfter = 0.0, srAfter = 0.0;
        {
            int activeClusters = 0;
            uint32_t coveredNodes = 0;
            double deliverySum = 0.0;
            int deliveryCount = 0;
            for(const auto& c : clustersAfter){
                if(c.leaderAlive){
                    activeClusters++;
                    coveredNodes += c.memberCount + 1;
                    if(c.initialMemberCount > 0){
                        double healthRatio = std::min(1.0, (double)c.memberCount / c.initialMemberCount);
                        double elapsed = (c.lastHeartbeat > 0) ?
                            (now - c.lastHeartbeat) : 120.0;
                        double linkQuality = std::max(0.1, 1.0 - std::min(1.0, elapsed / 120.0));
                        deliverySum += healthRatio * linkQuality;
                        deliveryCount++;
                    }
                }
            }
            double connectivity = clustersAfter.empty() ? 0.0 : 
                (double)activeClusters / clustersAfter.size();
            double propagation = totalNetworkNodes > 0 ? 
                std::min(1.0, (double)coveredNodes / totalNetworkNodes) : 0.0;
            double delivery = deliveryCount > 0 ? deliverySum / deliveryCount : 0.0;
            qiAfter = std::min(1.0, 0.5 * connectivity + 0.3 * propagation + 0.2 * delivery);
            srAfter = totalNetworkNodes > 0 ? 
                std::min(1.0, (double)coveredNodes / totalNetworkNodes) : 0.0;
        }

        intuitiveEngine->postActionUpdate(qiAfter, srAfter, snap.totalOrphans > 0);

        // [7] Agendar próximo ciclo
        Simulator::Schedule(Seconds(this->decisionInterval), 
                           &NodeAPApplication::intuitiveDecisionCycle, this);
    }

    // ============================================================
    // Processamento de órfãos com decisão individual por órfão
    // Integra RL3-style per-orphan iteration com o framework S1/S2
    // ============================================================
    void NodeAPApplication::processOrphansIntuitively(IntuitiveSnapshot& snap){
        double now = Simulator::Now().GetSeconds();

        // Constantes de recompensa proporcional (RL3.1-style)
        const double REWARD_BASE    = 10.0;   // Base, modulada por similaridade real
        const double REWARD_INVALID = -10.0;
        const double REWARD_ORPHAN  = -5.0;

        // [1] Coletar órfãos reais: nós cujo myLeader está morto
        struct OrphanInfo {
            Ipv6Address addr;
            Ptr<NodeApplication> app;
            capabilitiesVector caps;
        };
        std::vector<OrphanInfo> allOrphans;
        std::set<Ipv6Address> collectedAddrs;

        // Construir set de líderes mortos para lookup rápido
        std::set<Ipv6Address> deadLeaders;
        for(const auto& entry : clusterInfoMap){
            if(!entry.second.leaderAlive){
                deadLeaders.insert(entry.first);
            }
        }

        // Fonte 1: iterar todos os nós — órfão é quem tem myLeader morto
        for(uint32_t j = 0; j < this->networkNodes.GetN(); j++){
            Ptr<Node> node = this->networkNodes.Get(j);
            Ptr<Ipv6> ipv6 = node->GetObject<Ipv6>();
            Ipv6Address nodeAddr = ipv6->GetAddress(1, 0).GetAddress();

            Ptr<NodeApplication> nodeApp = DynamicCast<NodeApplication>(
                node->GetApplication(0));
            if(!nodeApp || !nodeApp->isNodeAlive()) continue;

            Ipv6Address leader = nodeApp->getMyLeader();
            // Nó é órfão se seu líder está no set de mortos
            // (e não é o próprio líder morto, que já foi desligado)
            if(deadLeaders.count(leader) > 0){
                if(collectedAddrs.count(nodeAddr) > 0) continue;
                OrphanInfo oi;
                oi.addr = nodeAddr;
                oi.app = nodeApp;
                oi.caps = nodeApp->getNodeCapabilities();
                allOrphans.push_back(oi);
                collectedAddrs.insert(nodeAddr);
            }
        }

        // Fonte 2: pendingOrphans (de ciclos anteriores)
        for(const auto& orphanAddr : pendingOrphans){
            if(collectedAddrs.count(orphanAddr) > 0) continue;
            Ptr<Node> orphanNode = findNodeByAddress(this->networkNodes, orphanAddr);
            if(!orphanNode) continue;
            Ptr<NodeApplication> orphanApp = DynamicCast<NodeApplication>(
                orphanNode->GetApplication(0));
            if(!orphanApp || !orphanApp->isNodeAlive()) continue;

            OrphanInfo oi;
            oi.addr = orphanAddr;
            oi.app = orphanApp;
            oi.caps = orphanApp->getNodeCapabilities();
            allOrphans.push_back(oi);
            collectedAddrs.insert(orphanAddr);
        }

        snap.totalOrphans = allOrphans.size();

        if(allOrphans.empty()){
            NS_LOG_INFO("INTUITIVE_ORPHANS: Nenhum órfão para processar");
            return;  // caller fará o push do snapshot após [5b]
        }

        // [2] Coletar clusters ativos e suas capacidades
        struct ActiveCluster {
            Ipv6Address leaderAddr;
            Ptr<NodeApplication> leaderApp;
            capabilitiesVector caps;
        };
        std::vector<ActiveCluster> activeClusters;
        for(const auto& entry : clusterInfoMap){
            if(!entry.second.leaderAlive) continue;
            Ptr<Node> leaderNode = findNodeByAddress(this->networkNodes, entry.first);
            if(!leaderNode) continue;
            Ptr<NodeApplication> leaderApp = DynamicCast<NodeApplication>(
                leaderNode->GetApplication(0));
            if(!leaderApp || !leaderApp->isNodeAlive()) continue;

            ActiveCluster ac;
            ac.leaderAddr = entry.first;
            ac.leaderApp = leaderApp;
            ac.caps = leaderApp->getNodeCapabilities();
            activeClusters.push_back(ac);
        }

        // [3] Calcular similaridade média entre todos os órfãos (para contexto RECLUSTER)
        // Pré-computar para eficiência
        std::map<Ipv6Address, double> bestSimWithOrphans;
        for(size_t i = 0; i < allOrphans.size(); i++){
            double maxSim = 0.0;
            for(size_t j = 0; j < allOrphans.size(); j++){
                if(i == j) continue;
                capabilitiesVector capI = allOrphans[i].caps;
                capabilitiesVector capJ = allOrphans[j].caps;
                double sim = capabilitiesSimilarity(&capI, &capJ);
                if(sim > maxSim) maxSim = sim;
            }
            bestSimWithOrphans[allOrphans[i].addr] = maxSim;
        }

        // [4] Iterar sobre cada órfão: avaliar estado, S1/S2, escolher ação
        std::vector<Ipv6Address> newClusterCandidates;  // Órfãos marcados para RECLUSTER
        std::vector<Ipv6Address> stillOrphaned;          // Órfãos não resolvidos

        double qi = intuitiveEngine->getLastQI();
        double sr = intuitiveEngine->getLastSR();
        double pThreat = intuitiveEngine->getLastPThreat();

        auto& dualSys = intuitiveEngine->getDualSystemMut();
        auto& knowledge = intuitiveEngine->getKnowledge();
        auto& distLearn = intuitiveEngine->getDistributedLearning();

        uint32_t s1CountBefore = dualSys.getS1Count();
        uint32_t s2CountBefore = dualSys.getS2Count();

        for(auto& orphan : allOrphans){
            // [4a] Calcular similaridade com melhor cluster existente
            double bestSimExisting = 0.0;
            int bestClusterIdx = -1;
            for(size_t i = 0; i < activeClusters.size(); i++){
                capabilitiesVector orphanCaps = orphan.caps;
                capabilitiesVector leaderCaps = activeClusters[i].caps;
                double sim = capabilitiesSimilarity(&orphanCaps, &leaderCaps);
                if(sim > bestSimExisting){
                    bestSimExisting = sim;
                    bestClusterIdx = i;
                }
            }

            // [4b] Similaridade com outros órfãos (pré-computado)
            double simOrphans = bestSimWithOrphans[orphan.addr];

            // [4c] Determinar estado do órfão
            OrphanState orphanState = DualSystemResponse::determineOrphanState(
                bestSimExisting, simOrphans, REALLOCATION_THRESHOLD, CLUSTERING_THRESHOLD);

            // [4d] S1/S2 escolhe ação para este órfão
            auto result = dualSys.chooseActionForOrphan(
                knowledge, distLearn, qi, sr, pThreat, orphanState);
            IntuitiveAction action = result.first;
            SystemUsed system = result.second;

            // [4e] Validar viabilidade e aplicar fallback (como RL3)
            bool existingViable = (bestSimExisting >= REALLOCATION_THRESHOLD && bestClusterIdx >= 0);
            bool orphansViable  = (simOrphans >= CLUSTERING_THRESHOLD);

            if(action == REALLOCATE_TO_EXISTING && !existingViable){
                action = orphansViable ? RECLUSTER_ORPHANS : DO_NOTHING;
                NS_LOG_INFO("INTUITIVE_FALLBACK: REALLOCATE inviável para " << orphan.addr
                            << ", fallback para " << IntuitiveActionNames[action]);
            } else if(action == RECLUSTER_ORPHANS && !orphansViable){
                action = existingViable ? REALLOCATE_TO_EXISTING : DO_NOTHING;
                NS_LOG_INFO("INTUITIVE_FALLBACK: RECLUSTER inviável para " << orphan.addr
                            << ", fallback para " << IntuitiveActionNames[action]);
            }

            // [4f] Executar ação e calcular recompensa PROPORCIONAL (RL3.1-style)
            // Reward = REWARD_BASE × similaridade × qualityFactor + bônus contextual
            // Isso dá gradiente: sim=0.98 vale mais que sim=0.87 para a mesma ação
            double reward = 0.0;
            bool resolved = false;

            if(action == REALLOCATE_TO_EXISTING){
                if(existingViable){
                    activeClusters[bestClusterIdx].leaderApp->addClusterMember(orphan.addr);
                    orphan.app->setMyLeader(activeClusters[bestClusterIdx].leaderAddr);
                    auto it = clusterInfoMap.find(activeClusters[bestClusterIdx].leaderAddr);
                    if(it != clusterInfoMap.end()){
                        it->second.memberCount++;
                    }

                    // Recompensa proporcional à qualidade do match individual
                    // Sem bônus artificial — a similaridade per-orphan é o sinal correto
                    reward = REWARD_BASE * bestSimExisting;

                    resolved = true;
                    snap.actionsReallocate++;
                    NS_LOG_INFO("INTUITIVE_ORPHAN: " << orphan.addr << " [" << OrphanStateNames[orphanState]
                                << "] → REALLOCATE para " << activeClusters[bestClusterIdx].leaderAddr
                                << " (sim=" << bestSimExisting << " r=" << reward << ") [" << SystemUsedNames[system] << "]");
                } else {
                    reward = REWARD_INVALID;
                }
            } else if(action == RECLUSTER_ORPHANS){
                if(orphansViable){
                    newClusterCandidates.push_back(orphan.addr);

                    // Recompensa proporcional à similaridade com outros órfãos
                    // Sem bônus artificial — a similaridade per-orphan é o sinal correto
                    reward = REWARD_BASE * simOrphans;

                    resolved = true;
                    snap.actionsRecluster++;
                    NS_LOG_INFO("INTUITIVE_ORPHAN: " << orphan.addr << " [" << OrphanStateNames[orphanState]
                                << "] → RECLUSTER (simOrphans=" << simOrphans << " r=" << reward << ") [" << SystemUsedNames[system] << "]");
                } else {
                    reward = REWARD_INVALID;
                }
            } else {
                // DO_NOTHING
                reward = REWARD_ORPHAN;
                snap.actionsDoNothing++;
                NS_LOG_INFO("INTUITIVE_ORPHAN: " << orphan.addr << " [" << OrphanStateNames[orphanState]
                            << "] → DO_NOTHING [" << SystemUsedNames[system] << "]");
            }

            // [4g] Atualizar Q-Table e Rintuitive para este órfão
            dualSys.updateQForOrphan(orphanState, action, reward);
            std::string bucket = OrphanStateNames[orphanState];
            knowledge.updateIntuitive(bucket, action, reward);

            if(!resolved){
                stillOrphaned.push_back(orphan.addr);
            }
        }

        // [5] Processar candidatos a reclusterização (fase coletiva)
        if(!newClusterCandidates.empty()){
            NS_LOG_INFO("INTUITIVE_RECLUSTER: " << newClusterCandidates.size()
                        << " órfãos marcados para reclusterização");

            // Coletar info dos candidatos
            struct ReclusterOrphan {
                Ipv6Address addr;
                Ptr<NodeApplication> app;
                capabilitiesVector caps;
            };
            std::vector<ReclusterOrphan> reclusterOrphans;
            for(const auto& addr : newClusterCandidates){
                Ptr<Node> node = findNodeByAddress(this->networkNodes, addr);
                if(!node) continue;
                Ptr<NodeApplication> app = DynamicCast<NodeApplication>(node->GetApplication(0));
                if(!app || !app->isNodeAlive()) continue;

                ReclusterOrphan ro;
                ro.addr = addr;
                ro.app = app;
                ro.caps = app->getNodeCapabilities();
                reclusterOrphans.push_back(ro);
            }

            // Agrupar por similaridade (mesmo algoritmo que o RECLUSTER original)
            std::vector<bool> assigned(reclusterOrphans.size(), false);
            int newClustersFormed = 0;

            for(size_t i = 0; i < reclusterOrphans.size(); i++){
                if(assigned[i]) continue;

                std::vector<size_t> clusterIndices;
                clusterIndices.push_back(i);
                assigned[i] = true;

                for(size_t j = i + 1; j < reclusterOrphans.size(); j++){
                    if(assigned[j]) continue;
                    capabilitiesVector capI = reclusterOrphans[i].caps;
                    capabilitiesVector capJ = reclusterOrphans[j].caps;
                    double sim = capabilitiesSimilarity(&capI, &capJ);
                    if(sim >= CLUSTERING_THRESHOLD){
                        clusterIndices.push_back(j);
                        assigned[j] = true;
                    }
                }

                // Precisa de pelo menos 2 nós para formar cluster
                if(clusterIndices.size() < 2){
                    assigned[i] = false;
                    continue;
                }

                // Eleger líder: órfão com mais capacidades (desempate por IP)
                size_t leaderIdx = clusterIndices[0];
                for(size_t k = 1; k < clusterIndices.size(); k++){
                    size_t candidateIdx = clusterIndices[k];
                    if(reclusterOrphans[candidateIdx].caps.size() > reclusterOrphans[leaderIdx].caps.size()){
                        leaderIdx = candidateIdx;
                    } else if(reclusterOrphans[candidateIdx].caps.size() == reclusterOrphans[leaderIdx].caps.size()){
                        uint8_t bufA[16], bufB[16];
                        reclusterOrphans[candidateIdx].addr.GetBytes(bufA);
                        reclusterOrphans[leaderIdx].addr.GetBytes(bufB);
                        if(memcmp(bufA, bufB, 16) < 0){
                            leaderIdx = candidateIdx;
                        }
                    }
                }

                // Promover líder e registrar novo cluster
                Ipv6Address newLeaderAddr = reclusterOrphans[leaderIdx].addr;
                reclusterOrphans[leaderIdx].app->becomeLeader(this->GetNodeIpAddress());

                for(size_t k = 0; k < clusterIndices.size(); k++){
                    size_t idx = clusterIndices[k];
                    if(idx == leaderIdx) continue;
                    reclusterOrphans[leaderIdx].app->addClusterMember(reclusterOrphans[idx].addr);
                    reclusterOrphans[idx].app->setMyLeader(newLeaderAddr);
                }

                ClusterInfo newInfo;
                newInfo.leaderAddr = newLeaderAddr;
                newInfo.leaderAlive = true;
                newInfo.memberCount = clusterIndices.size() - 1;
                newInfo.initialMemberCount = clusterIndices.size() - 1;
                newInfo.lastHeartbeat = now;
                newInfo.hasAcceptedTask = false;
                clusterInfoMap[newLeaderAddr] = newInfo;

                this->clusterLeaders->push_back(newLeaderAddr);
                intuitiveEngine->registerNode(newLeaderAddr, 0.7, true);

                newClustersFormed++;
                NS_LOG_INFO("INTUITIVE_RECLUSTER: Novo cluster - Líder " << newLeaderAddr
                            << " com " << (clusterIndices.size() - 1) << " seguidores");
            }

            // Órfãos marcados para RECLUSTER mas não agrupados → fallback para pendentes
            for(size_t i = 0; i < reclusterOrphans.size(); i++){
                if(!assigned[i]){
                    stillOrphaned.push_back(reclusterOrphans[i].addr);
                    NS_LOG_INFO("INTUITIVE_RECLUSTER_FALLBACK: " << reclusterOrphans[i].addr
                                << " não agrupado → pendente");
                }
            }

            NS_LOG_INFO("INTUITIVE_RECLUSTER: " << newClustersFormed << " novos clusters formados");
        }

        // [6] Limpar clusters mortos do mapa
        std::vector<Ipv6Address> deadToRemove;
        for(auto& entry : clusterInfoMap){
            if(entry.second.leaderAlive) continue;
            Ptr<Node> deadNode = findNodeByAddress(this->networkNodes, entry.first);
            if(deadNode){
                Ptr<NodeApplication> deadApp = DynamicCast<NodeApplication>(
                    deadNode->GetApplication(0));
                if(deadApp) deadApp->clearClusterMembers();
            }
            deadToRemove.push_back(entry.first);
        }
        for(const auto& addr : deadToRemove){
            clusterInfoMap.erase(addr);
        }

        // [7] Atualizar lista de pendentes
        pendingOrphans.clear();
        for(const auto& addr : stillOrphaned){
            pendingOrphans.push_back(addr);
        }

        // [8] Completar snapshot com contadores S1/S2 deste ciclo
        snap.cycleS1Count = dualSys.getS1Count() - s1CountBefore;
        snap.cycleS2Count = dualSys.getS2Count() - s2CountBefore;

        // O push do snapshot agora é responsabilidade do caller (após [5b])
        NS_LOG_INFO("INTUITIVE_ORPHANS: Processados " << allOrphans.size() << " órfãos"
                    << " | REALLOCATE=" << snap.actionsReallocate
                    << " RECLUSTER=" << snap.actionsRecluster
                    << " DO_NOTHING=" << snap.actionsDoNothing
                    << " | Pendentes=" << pendingOrphans.size()
                    << " | S1=" << snap.cycleS1Count << " S2=" << snap.cycleS2Count);
    }

    // ============================================================
    // v2 — Processamento de clusters degradados (passo [5b])
    // Para cada cluster vivo: computa capabilityCoverage + memberLossRate,
    // classifica estado (HEALTHY/DEGRADED/CRITICAL), Dual-System escolhe
    // ação (DO_NOTHING/REINFORCE/DISBAND), executa com fallback.
    // ============================================================
    void NodeAPApplication::processClusterDegradationIntuitively(IntuitiveSnapshot& snap){
        const double REWARD_BASE    = 10.0;
        const double REWARD_INVALID = -10.0;

        auto& clusterDualSys = intuitiveEngine->getClusterDualSystemMut();
        auto& knowledge      = intuitiveEngine->getKnowledge();
        auto& distLearn      = intuitiveEngine->getDistributedLearning();
        double qi      = intuitiveEngine->getLastQI();
        double sr      = intuitiveEngine->getLastSR();
        double pThreat = intuitiveEngine->getLastPThreat();

        uint32_t s1Before = clusterDualSys.getS1Count();
        uint32_t s2Before = clusterDualSys.getS2Count();

        // [5b.1] Coletar info por cluster vivo: líder, caps do líder, membros vivos
        struct ClusterCtx {
            Ipv6Address          leaderAddr;
            Ptr<NodeApplication> leaderApp;
            capabilitiesVector   leaderCaps;
            std::vector<Ipv6Address>      aliveMembers;       // não inclui o líder
            std::vector<capabilitiesVector> aliveMemberCaps;  // paralelo a aliveMembers
            uint32_t             initialMemberCount;
            double               capabilityCoverage;
            double               memberLossRate;
            ClusterDegradedState state;
        };
        std::vector<ClusterCtx> ctxs;
        ctxs.reserve(clusterInfoMap.size());

        for(auto& entry : clusterInfoMap){
            if(!entry.second.leaderAlive) continue;
            Ptr<Node> leaderNode = findNodeByAddress(this->networkNodes, entry.first);
            if(!leaderNode) continue;
            Ptr<NodeApplication> leaderApp = DynamicCast<NodeApplication>(
                leaderNode->GetApplication(0));
            if(!leaderApp || !leaderApp->isNodeAlive()) continue;

            ClusterCtx ctx;
            ctx.leaderAddr = entry.first;
            ctx.leaderApp  = leaderApp;
            ctx.leaderCaps = leaderApp->getNodeCapabilities();
            ctx.initialMemberCount = entry.second.initialMemberCount;

            for(uint32_t j = 0; j < this->networkNodes.GetN(); j++){
                Ptr<Node> n = this->networkNodes.Get(j);
                Ptr<NodeApplication> nApp = DynamicCast<NodeApplication>(n->GetApplication(0));
                if(!nApp || !nApp->isNodeAlive()) continue;
                Ipv6Address nAddr = nApp->GetNodeIpAddress();
                if(nAddr == ctx.leaderAddr) continue;
                if(nApp->getMyLeader() != ctx.leaderAddr) continue;

                ctx.aliveMembers.push_back(nAddr);
                ctx.aliveMemberCaps.push_back(nApp->getNodeCapabilities());
            }

            // [5b.2] Snapshot do MULTI-SET (count por cap) — 1x por cluster.
            // Permite computar perda gradual de redundância em [5b.3].
            if(originalClusterCapsCount.find(ctx.leaderAddr) == originalClusterCapsCount.end()
               && (ctx.aliveMembers.size() + 1) > 0){
                std::map<capabilities, int> origCount;
                for(auto c : ctx.leaderCaps) origCount[c]++;
                for(auto& mc : ctx.aliveMemberCaps)
                    for(auto c : mc) origCount[c]++;
                originalClusterCapsCount[ctx.leaderAddr] = origCount;
            }

            // [5b.3] Capability redundancy: média_cap(count_atual / count_original).
            // Mede perda de redundância (gradual) em vez de perda de presença (terminal).
            // Ex.: cap X tinha 10 portadores, agora tem 7 → r_X = 0.7.
            std::map<capabilities, int> currentCount;
            for(auto c : ctx.leaderCaps) currentCount[c]++;
            for(auto& mc : ctx.aliveMemberCaps)
                for(auto c : mc) currentCount[c]++;

            auto itOrig = originalClusterCapsCount.find(ctx.leaderAddr);
            if(itOrig == originalClusterCapsCount.end() || itOrig->second.empty()){
                ctx.capabilityCoverage = 1.0;
            } else {
                double sumRatio = 0.0;
                int nCaps = 0;
                for(const auto& kv : itOrig->second){
                    auto itC = currentCount.find(kv.first);
                    int curr = (itC == currentCount.end()) ? 0 : itC->second;
                    // Clampar em [0,1] (curr nunca > original em cenário membro-falha-só)
                    double r = (double)curr / (double)kv.second;
                    if(r > 1.0) r = 1.0;
                    sumRatio += r;
                    nCaps++;
                }
                ctx.capabilityCoverage = (nCaps > 0) ? sumRatio / nCaps : 1.0;
            }

            // [5b.4] memberLossRate = 1 - clusterHealthRatio
            if(ctx.initialMemberCount > 0){
                double health = std::min(1.0,
                    (double)ctx.aliveMembers.size() / (double)ctx.initialMemberCount);
                ctx.memberLossRate = 1.0 - health;
            } else {
                ctx.memberLossRate = 0.0;
            }

            ctx.state = ClusterDualSystemResponse::determineState(
                ctx.capabilityCoverage, ctx.memberLossRate);

            // Contadores de estado
            switch(ctx.state){
                case CLUSTER_HEALTHY:  snap.clustersHealthy++;  break;
                case CLUSTER_DEGRADED: snap.clustersDegraded++; break;
                case CLUSTER_CRITICAL: snap.clustersCritical++; break;
                default: break;
            }

            ctxs.push_back(ctx);
        }

        // [5b.5] Para cada cluster degradado/critical, escolher ação e executar
        // (HEALTHY entra como contador mas não dispara decisão)
        // Snapshot de tamanhos atuais para evitar tomar como doador um cluster
        // que acabou de ser drenado neste mesmo ciclo
        std::map<Ipv6Address, int> liveSizeMap;
        for(auto& c : ctxs) liveSizeMap[c.leaderAddr] = (int)c.aliveMembers.size();

        for(auto& ctx : ctxs){
            if(ctx.state == CLUSTER_HEALTHY) continue;

            // Dual-System decide ação
            auto result = clusterDualSys.chooseActionForCluster(
                knowledge, distLearn, qi, sr, pThreat, ctx.state);
            ClusterAction action = result.first;
            SystemUsed   system = result.second;

            // Validar viabilidade do REINFORCE: precisa de doador saudável similar
            // (sim>=0.85, com >= 2 membros vivos pra emprestar 1)
            int donorIdx = -1;
            double bestDonorSim = 0.0;
            int donorMemberIdx = -1;
            double bestMemberSim = 0.0;

            auto findDonor = [&](){
                donorIdx = -1; bestDonorSim = 0.0;
                donorMemberIdx = -1; bestMemberSim = 0.0;
                for(size_t i = 0; i < ctxs.size(); i++){
                    if(ctxs[i].leaderAddr == ctx.leaderAddr) continue;
                    if(ctxs[i].state != CLUSTER_HEALTHY) continue;
                    if(liveSizeMap[ctxs[i].leaderAddr] < 2) continue;  // não esvaziar doador

                    capabilitiesVector capA = ctx.leaderCaps;
                    capabilitiesVector capB = ctxs[i].leaderCaps;
                    double sim = capabilitiesSimilarity(&capA, &capB);
                    if(sim >= REALLOCATION_THRESHOLD && sim > bestDonorSim){
                        bestDonorSim = sim;
                        donorIdx = (int)i;
                    }
                }
                if(donorIdx < 0) return;
                // Escolher o membro do doador mais similar ao cluster degradado
                for(size_t k = 0; k < ctxs[donorIdx].aliveMemberCaps.size(); k++){
                    capabilitiesVector capM = ctxs[donorIdx].aliveMemberCaps[k];
                    capabilitiesVector capL = ctx.leaderCaps;
                    double simM = capabilitiesSimilarity(&capM, &capL);
                    if(simM > bestMemberSim){
                        bestMemberSim = simM;
                        donorMemberIdx = (int)k;
                    }
                }
            };

            if(action == CLUSTER_REINFORCE){
                findDonor();
                if(donorIdx < 0 || donorMemberIdx < 0){
                    // Sem doador → fallback (DISBAND se CRITICAL, senão DO_NOTHING)
                    ClusterAction fb = (ctx.state == CLUSTER_CRITICAL)
                        ? CLUSTER_DISBAND : CLUSTER_DO_NOTHING;
                    NS_LOG_INFO("CLUSTER_FALLBACK: REINFORCE inviável para " << ctx.leaderAddr
                                << " (sem doador) → " << ClusterActionNames[fb]);
                    action = fb;
                }
            }

            double reward = 0.0;
            bool resolved = false;

            if(action == CLUSTER_REINFORCE){
                Ipv6Address donorLeader  = ctxs[donorIdx].leaderAddr;
                Ipv6Address donorMember  = ctxs[donorIdx].aliveMembers[donorMemberIdx];

                // Transferir o membro: setMyLeader no membro + addClusterMember no novo líder
                Ptr<Node> mNode = findNodeByAddress(this->networkNodes, donorMember);
                if(mNode){
                    Ptr<NodeApplication> mApp = DynamicCast<NodeApplication>(mNode->GetApplication(0));
                    if(mApp){
                        mApp->setMyLeader(ctx.leaderAddr);
                        ctx.leaderApp->addClusterMember(donorMember);
                        ctxs[donorIdx].leaderApp->clearClusterMembers();
                        // Recompor lista do doador sem o membro emprestado
                        for(size_t k = 0; k < ctxs[donorIdx].aliveMembers.size(); k++){
                            if((int)k == donorMemberIdx) continue;
                            ctxs[donorIdx].leaderApp->addClusterMember(
                                ctxs[donorIdx].aliveMembers[k]);
                        }
                        liveSizeMap[donorLeader]--;
                        liveSizeMap[ctx.leaderAddr]++;
                        // Atualiza memberCount no clusterInfoMap dos dois envolvidos
                        auto itA = clusterInfoMap.find(ctx.leaderAddr);
                        if(itA != clusterInfoMap.end()) itA->second.memberCount++;
                        auto itB = clusterInfoMap.find(donorLeader);
                        if(itB != clusterInfoMap.end() && itB->second.memberCount > 0)
                            itB->second.memberCount--;

                        reward   = REWARD_BASE * bestDonorSim;
                        resolved = true;
                        snap.actionsReinforce++;
                        NS_LOG_INFO("CLUSTER_REINFORCE: " << ctx.leaderAddr
                                    << " [" << ClusterDegradedStateNames[ctx.state]
                                    << "] ← doador " << donorLeader
                                    << " membro " << donorMember
                                    << " (sim=" << bestDonorSim
                                    << " r=" << reward << ") ["
                                    << SystemUsedNames[system] << "]");
                    } else {
                        reward = REWARD_INVALID;
                    }
                } else {
                    reward = REWARD_INVALID;
                }
            }

            if(action == CLUSTER_DISBAND){
                // Dissolve o cluster: para o ex-líder (sua aplicação) e os
                // membros vivos viram órfãos no próximo ciclo (pendingOrphans).
                Ipv6Address exLeader = ctx.leaderAddr;

                // Marcar líder como morto e parar sua aplicação
                Ptr<Node> lNode = findNodeByAddress(this->networkNodes, exLeader);
                if(lNode){
                    Ptr<Application> app = lNode->GetApplication(0);
                    Simulator::ScheduleNow(&Application::SetStopTime, app, Simulator::Now());
                    ctx.leaderApp->StopApplication();
                }

                // Despejar membros vivos no pool de pendentes (serão órfãos no próximo ciclo
                // de [5] da v1, com myLeader apontando pro ex-líder agora morto)
                for(auto& m : ctx.aliveMembers){
                    pendingOrphans.push_back(m);
                }

                // Marcar cluster como morto no map para limpeza posterior
                auto itC = clusterInfoMap.find(exLeader);
                if(itC != clusterInfoMap.end()) itC->second.leaderAlive = false;

                // Notificar motor intuitivo
                intuitiveEngine->registerLeaderDeath(exLeader);

                reward   = REWARD_BASE * 0.5;  // ação destrutiva: ganho contido
                resolved = true;
                snap.actionsDisband++;
                NS_LOG_INFO("CLUSTER_DISBAND: " << exLeader
                            << " [" << ClusterDegradedStateNames[ctx.state]
                            << "] dissolvido, " << ctx.aliveMembers.size()
                            << " membros → pendingOrphans"
                            << " (r=" << reward << ") ["
                            << SystemUsedNames[system] << "]");
            }

            if(action == CLUSTER_DO_NOTHING){
                reward = -2.0;  // pequeno custo de não-ação em estado problemático
                snap.actionsClusterDoNothing++;
                NS_LOG_INFO("CLUSTER_DO_NOTHING: " << ctx.leaderAddr
                            << " [" << ClusterDegradedStateNames[ctx.state]
                            << "] (cov=" << ctx.capabilityCoverage
                            << " loss=" << ctx.memberLossRate
                            << ") [" << SystemUsedNames[system] << "]");
                resolved = true;
            }

            // Q-table e R_int (bucket prefixado pra não colidir com órfãos)
            clusterDualSys.updateQForCluster(ctx.state, action, reward);
            std::string bucket = std::string("CL_") + ClusterDegradedStateNames[ctx.state];
            // R_int usa ação como int genérico — armazena no mesmo mapa, mas o bucket
            // distinto evita confusão com ações de órfão
            knowledge.updateIntuitive(bucket, static_cast<IntuitiveAction>(action), reward);

            (void)resolved;  // suprimir warning unused
        }

        // [5b.6] Decaimento do epsilon do cluster dual-system (se houve degradação)
        if(snap.clustersDegraded + snap.clustersCritical > 0){
            clusterDualSys.decayEpsilon();
        }

        snap.cycleS1ClusterCount = clusterDualSys.getS1Count() - s1Before;
        snap.cycleS2ClusterCount = clusterDualSys.getS2Count() - s2Before;

        NS_LOG_INFO("CLUSTER_DEGRADATION: HEALTHY=" << snap.clustersHealthy
                    << " DEGRADED=" << snap.clustersDegraded
                    << " CRITICAL=" << snap.clustersCritical
                    << " | REINFORCE=" << snap.actionsReinforce
                    << " DISBAND=" << snap.actionsDisband
                    << " DO_NOTHING=" << snap.actionsClusterDoNothing
                    << " | S1c=" << snap.cycleS1ClusterCount
                    << " S2c=" << snap.cycleS2ClusterCount);
    }

    // ============================================================
    // v2 — Dump de ranking de risco (inferência subproduto)
    // Para cada ciclo, escreve Li ascendente: nós com Li baixo são candidatos
    // a falhar / requerem atenção. Sem custo extra (Li já é mantido).
    // ============================================================
    void NodeAPApplication::dumpRiskRanking(double timestamp){
        const auto& allNodes = intuitiveEngine->getDistributedLearning().getAllNodes();
        if(allNodes.empty()) return;

        struct Entry { Ipv6Address addr; double Li; bool isLeader; bool isAnomalous; };
        std::vector<Entry> rows;
        rows.reserve(allNodes.size());
        for(const auto& kv : allNodes){
            if(!kv.second.alive) continue;
            Entry e{kv.first, kv.second.Li, kv.second.isLeader, kv.second.isAnomalous};
            rows.push_back(e);
        }
        std::sort(rows.begin(), rows.end(),
                  [](const Entry& a, const Entry& b){ return a.Li < b.Li; });

        // Append no arquivo único da rodada para todos os ciclos
        static bool headerWritten = false;
        std::ofstream out("RiskRanking.csv", std::ios::app);
        if(!headerWritten){
            out << "timestamp,rank,address,Li,isLeader,isAnomalous" << std::endl;
            headerWritten = true;
        }
        int rank = 1;
        for(const auto& r : rows){
            out << timestamp << "," << rank++ << "," << r.addr << ","
                << r.Li << "," << (r.isLeader?1:0) << "," << (r.isAnomalous?1:0)
                << std::endl;
        }
        out.close();
    }

    std::vector<ClusterInfo> NodeAPApplication::buildClusterInfoVector() const {
        std::vector<ClusterInfo> result;
        for(const auto& entry : clusterInfoMap){
            result.push_back(entry.second);
        }
        return result;
    }

    void NodeAPApplication::processHeartbeat(Ipv6Address leaderAddr, uint32_t currentMembers){
        auto it = clusterInfoMap.find(leaderAddr);
        if(it != clusterInfoMap.end()){
            it->second.memberCount = currentMembers;
            it->second.lastHeartbeat = Simulator::Now().GetSeconds();
        }
    }

    void NodeAPApplication::updateClusterInfo(Ipv6Address leader, uint32_t memberCount,
                                               uint32_t initialMemberCount){
        auto it = clusterInfoMap.find(leader);
        if(it != clusterInfoMap.end()){
            it->second.memberCount = memberCount;
            if(it->second.initialMemberCount == 0){
                it->second.initialMemberCount = initialMemberCount;
            }
        }
    }

    void NodeAPApplication::writeIntuitiveLog(){
        const auto& history = intuitiveEngine->getHistory();
        if(history.empty()) return;

        ofstream logFile("IntuitiveStats.csv");
        logFile << "timestamp,qi,sr,tii,arf,meanRho,netLearning,pThreat,"
                << "totalOrphans,actionsReallocate,actionsRecluster,actionsDoNothing,"
                << "cycleS1,cycleS2,anomalies,qDoNothing,qReallocate,qRecluster,"
                // v2: campos da decisão por cluster
                << "clustersHealthy,clustersDegraded,clustersCritical,"
                << "actionsReinforce,actionsDisband,actionsClusterDoNothing,"
                << "cycleS1Cluster,cycleS2Cluster,"
                << "qClusterDoNothing,qReinforce,qDisband"
                << std::endl;

        for(const auto& snap : history){
            logFile << snap.timestamp << ","
                    << snap.qi << ","
                    << snap.sr << ","
                    << snap.tii << ","
                    << snap.arf << ","
                    << snap.meanRho << ","
                    << snap.netLearning << ","
                    << snap.pThreat << ","
                    << snap.totalOrphans << ","
                    << snap.actionsReallocate << ","
                    << snap.actionsRecluster << ","
                    << snap.actionsDoNothing << ","
                    << snap.cycleS1Count << ","
                    << snap.cycleS2Count << ","
                    << snap.anomalies << ","
                    << snap.qDoNothing << ","
                    << snap.qReallocate << ","
                    << snap.qRecluster << ","
                    // v2
                    << snap.clustersHealthy << ","
                    << snap.clustersDegraded << ","
                    << snap.clustersCritical << ","
                    << snap.actionsReinforce << ","
                    << snap.actionsDisband << ","
                    << snap.actionsClusterDoNothing << ","
                    << snap.cycleS1ClusterCount << ","
                    << snap.cycleS2ClusterCount << ","
                    << snap.qClusterDoNothing << ","
                    << snap.qReinforce << ","
                    << snap.qDisband
                    << std::endl;
        }
        logFile.close();

        ofstream qFile("IntuitiveQValues.txt");
        qFile << "=== Q-VALORES FINAIS (4x3) ===" << std::endl;
        for(int s = 0; s < ORPHAN_STATE_COUNT; s++){
            qFile << "Estado " << OrphanStateNames[s] << ":" << std::endl;
            for(int a = 0; a < INTUITIVE_ACTION_COUNT; a++){
                qFile << "  " << IntuitiveActionNames[a] << ": " 
                      << intuitiveEngine->getDualSystem().getQValue(
                            static_cast<OrphanState>(s),
                            static_cast<IntuitiveAction>(a))
                      << std::endl;
            }
        }
        qFile << "Epsilon final: " << intuitiveEngine->getDualSystem().getEpsilon() << std::endl;
        qFile << "S1 count total: " << intuitiveEngine->getDualSystem().getS1Count() << std::endl;
        qFile << "S2 count total: " << intuitiveEngine->getDualSystem().getS2Count() << std::endl;

        // v2: Q-table do cluster dual-system
        qFile << std::endl << "=== Q-VALORES CLUSTER (3x3) ===" << std::endl;
        for(int s = 0; s < CLUSTER_DEGRADED_STATE_COUNT; s++){
            qFile << "Estado " << ClusterDegradedStateNames[s] << ":" << std::endl;
            for(int a = 0; a < CLUSTER_ACTION_COUNT; a++){
                qFile << "  " << ClusterActionNames[a] << ": "
                      << intuitiveEngine->getClusterDualSystem().getQValue(
                            static_cast<ClusterDegradedState>(s),
                            static_cast<ClusterAction>(a))
                      << std::endl;
            }
        }
        qFile << "Epsilon cluster final: " << intuitiveEngine->getClusterDualSystem().getEpsilon() << std::endl;
        qFile << "S1 cluster count total: " << intuitiveEngine->getClusterDualSystem().getS1Count() << std::endl;
        qFile << "S2 cluster count total: " << intuitiveEngine->getClusterDualSystem().getS2Count() << std::endl;
        qFile.close();

        NS_LOG_INFO("INTUITIVE: Logs escritos em IntuitiveStats.csv, IntuitiveQValues.txt, RiskRanking.csv");
    }
}