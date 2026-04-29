#pragma once

#include "ns3/core-module.h"
#include "ns3/application.h"
#include "ns3/socket.h"

#include "capabilities.h"
#include "task.h"
#include "constants.h"

using namespace ns3;

namespace nr2{
    class NodeApplication : public Application{
        private:
            std::map<Ipv6Address, int>*                             neighList;
            std::map<Ipv6Address, int>*                             clusterList;

            std::map< Ipv6Address, capabilitiesVector>*             neighCapabilities;
            std::vector< std::pair<double, Ipv6Address>* >*          neighSimilatiries;
            Ptr<Socket>                                             m_socket;       	// Associated socket
            Address                                                 m_node;				// Node's
            TypeId                                                  m_tid;          	// Type of the socket used
            Address                                                 leaderNode;
            Ipv6Address                                             apAddress;

            capabilitiesVector*                                     capabilities;
            bool                                                    isLeader;
            capabilitiesVector*                                     clusterCapabilities;
            double                                                  delay;

            std::vector<Ipv6Address>                                allNodesAddrs;

            // Flag para indicar se o nó está ativo
            bool                                                    alive;

            // Intervalo de heartbeat para líderes (segundos)
            double                                                  heartbeatInterval;

            // Líder eleito por este nó (relação exclusiva 1:1)
            Ipv6Address                                             myLeader;

        public:
            void setup(capabilitiesVector cap);
            void recvCallback(Ptr<Socket> socket);
            void StartApplication();
            void StopApplication();
            static TypeId GetTypeId();
            Ipv6Address GetNodeIpAddress();

            void beacon();
            void disseminateCapabilities();
            void similarityCalculation();
            void doClustering();
            float getSimilarityMode();
            void selectAndRegisterLeader();
            void registerLeader();

            Ipv6Address tiebreakLeader();
            void setAPAddress(Ipv6Address ip);
            void dispatchTaskToCluster(string cap);
            int sendMessageHelper(MessageTypes type, Ipv6Address addr, uint8_t* buffer, int size);
            void sendBroadcastMessageHelper(MessageTypes type, uint8_t* buffer, int size);
            void performTask(string);

            void nullFunction();
            void setAllNodesAddrs(std::vector<Ipv6Address>);
            void setDelay(double);

            // Método para obter tamanho do cluster (nós órfãos em caso de falha)
            int getClusterSize();

            // Obter endereços dos membros do cluster
            std::vector<Ipv6Address> getClusterMembers();

            // Adicionar membro ao cluster (usado pelo AP na realocação)
            void addClusterMember(Ipv6Address member);

            // Verificar se o nó está ativo
            bool isNodeAlive() const { return alive; }

            // Enviar heartbeat periódico ao AP (apenas líderes)
            void sendHeartbeat();

            // Limpar lista de membros do cluster (usado após realocação)
            void clearClusterMembers();

            // Obter capacidades do nó (para cálculo de similaridade na realocação)
            capabilitiesVector getNodeCapabilities() const;

            // Promover nó a líder de novo cluster (usado na reclusterização)
            void becomeLeader(Ipv6Address apAddr);

            // Líder ao qual este nó pertence (relação exclusiva)
            Ipv6Address getMyLeader() const { return myLeader; }
            void setMyLeader(Ipv6Address leader) { myLeader = leader; }

    };
}