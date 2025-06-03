# Sistema de Chat Distribuído com Ordenação de Mensagens por Relógios Vetoriais

## 1. Visão Geral do Problema

Em sistemas de comunicação distribuídos, como chats com múltiplos participantes, um desafio fundamental é a ordenação de mensagens. Sem um relógio global perfeitamente sincronizado, diferentes participantes podem receber mensagens em ordens distintas daquelas em que foram enviadas, especialmente devido a latências de rede variáveis e ao processamento descentralizado. Isso pode levar a conversas confusas ou mal interpretadas, onde a relação causal entre as mensagens não é preservada na visualização.

Este projeto visa demonstrar uma solução para este problema através da implementação de um sistema de chat em grupo que utiliza **Relógios Vetoriais** para estabelecer uma ordem causal entre as mensagens, permitindo que cada participante possa, potencialmente, reconstruir uma visão consistente da conversa.

## 2. Solução Implementada

Foi desenvolvido um sistema de chat distribuído funcional utilizando Python e gRPC. A principal característica explorada e implementada é o uso de **Relógios Vetoriais** para a ordenação de eventos (envio e recebimento de mensagens) em um ambiente distribuído.

A arquitetura simplificada consiste em:
* **Dois Clientes:** Representam os participantes do chat. Cada cliente opera como um processo independente.
* **Um Servidor Central:** Atua como um ponto de encontro para os clientes, retransmitindo mensagens e participando do sistema de relógios vetoriais.

O sistema permite que os clientes se inscrevam em um grupo de chat, enviem mensagens para este grupo e recebam as mensagens enviadas por outros membros, com cada mensagem sendo carimbada com o Relógio Vetorial do remetente.

## 3. Detalhes Técnicos

### 3.1. Tecnologias Utilizadas
* **Python 3.x:** Linguagem de programação principal.
* **gRPC:** Framework de RPC (Remote Procedure Call) de alta performance para comunicação entre clientes e servidor.
    * Utiliza HTTP/2 para transporte.
* **Protocol Buffers (Protobuf v3):** Mecanismo de serialização de dados estruturados, usado para definir as mensagens e os serviços gRPC.
* **Threading (Python `threading`):** Usado no cliente para permitir o envio e recebimento de mensagens concorrentemente (interface de linha de comando não bloqueante).

### 3.2. Estrutura do Projeto
O projeto é composto pelos seguintes arquivos principais:
* `chat.proto`: Define a estrutura das mensagens (como `ChatMessage`, `VectorClock`) e os serviços gRPC (`ChatService` com os métodos `SendMessage` e `SubscribeToGroup`).
* `vector_clock_manager.py`: Contém a classe `VectorClockManager`, responsável pela lógica de inicialização, incremento e atualização dos relógios vetoriais em cada processo.
* `server.py`: Implementação do servidor gRPC. Ele gerencia os grupos, os clientes inscritos, retransmite mensagens e mantém seu próprio relógio vetorial.
* `client.py`: Implementação do cliente gRPC. Permite ao usuário enviar mensagens, recebe mensagens de outros clientes (via servidor) e gerencia seu relógio vetorial.
* `chat_pb2.py`, `chat_pb2_grpc.py`: Arquivos Python gerados automaticamente pelo compilador `protoc` a partir do `chat.proto`.

### 3.3. Implementação dos Relógios Vetoriais
* **Conceito:** Cada processo no sistema (2 clientes e 1 servidor, totalizando N=3 processos) mantém um vetor de inteiros de tamanho N. O i-ésimo componente do vetor no processo Pj representa o conhecimento que Pj tem sobre o número de eventos ocorridos no processo Pi.
* **Identificação dos Processos:**
    * Cliente A: ID de processo `0`
    * Cliente B: ID de processo `1`
    * Servidor: ID de processo `2`
* **Lógica:**
    1.  **Inicialização:** Cada processo inicia com seu vetor zerado (ex: `[0,0,0]`).
    2.  **Envio de Mensagem:** Antes de um processo $P_i$ enviar uma mensagem, ele incrementa sua própria posição $i$ no seu vetor ($VC_i[i]++$). A mensagem é enviada com uma cópia deste vetor $VC_i$.
    3.  **Recebimento de Mensagem:** Quando um processo $P_j$ recebe uma mensagem de $P_i$ contendo um vetor $VC_{msg}$:
        * $P_j$ atualiza cada elemento $k$ do seu vetor para o máximo entre seu valor atual e o valor correspondente no vetor recebido ($VC_j[k] = \max(VC_j[k], VC_{msg}[k])$).
        * Em seguida, $P_j$ incrementa sua própria posição $j$ no seu vetor ($VC_j[j]++$) para registrar o evento de recebimento/processamento da mensagem.
    Esta lógica é encapsulada na classe `VectorClockManager`.

### 3.4. Comunicação gRPC
* **Definições (`chat.proto`):**
    * `VectorClock`: Mensagem contendo um campo `repeated int32 clock`.
    * `ChatMessage`: Mensagem contendo `user_id`, `text`, `VectorClock vector_clock`, e `group_id`.
    * `SubscriptionRequest`: Usada pelo cliente para se inscrever, contendo `user_id`, `client_process_id`, e `group_id`.
    * `ChatService`:
        * `rpc SendMessage(ChatMessage) returns (google.protobuf.Empty)`: RPC unário para clientes enviarem mensagens.
        * `rpc SubscribeToGroup(SubscriptionRequest) returns (stream ChatMessage)`: RPC server-streaming para clientes receberem mensagens do grupo.
* **Servidor (`server.py`):**
    * Mantém uma lista de assinantes para cada grupo.
    * No `SendMessage`: atualiza seu VCM e reencaminha (enfileira) a mensagem original para as filas dos clientes inscritos no grupo.
    * No `SubscribeToGroup`: registra o cliente e mantém uma stream aberta, enviando mensagens da fila específica daquele cliente via `yield`.
* **Cliente (`client.py`):**
    * Usa uma thread separada para chamar `SubscribeToGroup` e processar mensagens recebidas continuamente.
    * A thread principal permite ao usuário digitar e enviar mensagens via `SendMessage`.

## 4. Como Executar o Sistema

### 4.1. Pré-requisitos
* Python 3.7+
* Pip (gerenciador de pacotes Python)

### 4.2. Instalação de Dependências
Navegue até o diretório raiz do projeto e execute:
```bash
pip install grpcio grpcio-tools
```

### 4.3. Geração dos Stubs gRPC (se ainda não gerados)
Se os arquivos `chat_pb2.py` e `chat_pb2_grpc.py` não estiverem presentes ou se você modificar `chat.proto`, execute:
```bash
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. chat.proto
```

### 4.4. Executando o Servidor
Abra um terminal, navegue até o diretório do projeto e execute:
```bash
python server.py
```
O servidor iniciará e aguardará conexões na porta `localhost:50051`.

### 4.5. Executando os Clientes
Você precisará de dois terminais separados para rodar os dois clientes.

* **Cliente A (Processo 0):**
    No primeiro terminal de cliente, execute:
    ```bash
    python client.py
    ```
    Quando solicitado, digite `0` para o "ID do Processo deste Cliente".

* **Cliente B (Processo 1):**
    No segundo terminal de cliente, execute:
    ```bash
    python client.py
    ```
    Quando solicitado, digite `1` para o "ID do Processo deste Cliente".

Agora você pode enviar mensagens de qualquer cliente. As mensagens serão exibidas em ambos os clientes, juntamente com o log dos relógios vetoriais, demonstrando a atualização dos mesmos em cada evento de envio e recebimento.

## 5. TODO - Possíveis Melhorias e Extensões Futuras

Embora o sistema atual demonstre o conceito de relógios vetoriais, diversas melhorias podem ser implementadas:

* **Interface Gráfica (GUI):** Substituir a interface de linha de comando por uma GUI mais amigável (ex: com Tkinter, PyQt, Kivy, ou uma interface web).
* **Número Dinâmico de Processos:** Adaptar o sistema para suportar um número dinâmico de clientes entrando e saindo, o que exigiria um gerenciamento mais complexo do tamanho e da consistência dos relógios vetoriais.
* **Persistência de Mensagens:** Salvar as mensagens em um banco de dados ou arquivo para que o histórico da conversa não seja perdido quando os clientes/servidor são encerrados.
* **Tratamento de Falhas:**
    * Melhorar a robustez do servidor e do cliente a desconexões inesperadas.
    * Implementar mecanismos de detecção de falhas e, possivelmente, recuperação.
* **Ordenação de Exibição Baseada em VCs:** Atualmente, as mensagens são exibidas conforme chegam, e o relógio vetorial é logado. Uma melhoria seria o cliente *bufferizar* mensagens e tentar reordená-las para exibição com base na ordem causal determinada pelos VCs (especialmente para lidar com mensagens que chegam "fora de ordem" devido à rede).
* **Segurança:** Implementar comunicação segura usando SSL/TLS para gRPC.
* **Escalabilidade do Servidor:** Para um grande número de usuários, o servidor único pode se tornar um gargalo. Considerar arquiteturas com múltiplos servidores (exigindo coordenação mais complexa).
* **Gerenciamento de Grupos:** Permitir a criação de múltiplos grupos, listagem de grupos, convites, etc.
* **Testes:** Adicionar testes unitários e de integração para garantir a corretude e robustez do sistema.
* **Entrega Garantida de Mensagens:** Explorar mecanismos para garantir que as mensagens não sejam perdidas em caso de falhas temporárias.
* **Manuseio de Concorrência Avançado:** Usar `asyncio` com gRPC para uma E/S mais eficiente, especialmente no servidor, em vez de um pool de threads síncrono.