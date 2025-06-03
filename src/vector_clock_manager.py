# Importamos a mensagem VectorClock gerada pelo protoc,
# para que possamos facilmente converter nosso relógio para o formato protobuf.
from chat_pb2 import VectorClock

class VectorClockManager:
    def __init__(self, process_id: int, num_processes: int):
        """
        Inicializa o gerenciador de relógio vetorial.

        Args:
            process_id: O ID deste processo (ex: 0 para ClienteA, 1 para ClienteB, 2 para Servidor).
            num_processes: O número total de processos no sistema (N=3 no nosso caso).
        """
        if not (0 <= process_id < num_processes):
            raise ValueError("process_id deve estar entre 0 e num_processes-1")

        self.process_id = process_id
        self.num_processes = num_processes
        # Inicializa o relógio vetorial com zeros. Ex: [0, 0, 0] para N=3.
        self.clock = [0] * num_processes
        print(f"Processo {self.process_id}: Relógio inicializado como {self.clock}")

    def increment(self):
        """
        Incrementa o contador do processo local no relógio vetorial.
        Isso deve ser chamado quando o processo realiza um evento significativo
        (ex: antes de enviar uma mensagem ou após concluir uma operação interna).
        """
        self.clock[self.process_id] += 1
        print(f"Processo {self.process_id}: Relógio incrementado para {self.clock}")

    def update(self, received_clock_list: list[int]):
        """
        Atualiza o relógio vetorial local com base em um relógio vetorial recebido.
        Isso deve ser chamado quando o processo recebe uma mensagem contendo um relógio vetorial.

        Args:
            received_clock_list: A lista de inteiros do VectorClock da mensagem recebida.
                                (ex: [1, 0, 0] vindo de outra mensagem).
        """
        if len(received_clock_list) != self.num_processes:
            print(f"Processo {self.process_id}: Erro - Relógio recebido {received_clock_list} tem tamanho inválido. Esperado: {self.num_processes}")
            # Em um sistema real, você poderia levantar uma exceção ou ter uma política de erro.
            # Por simplicidade, vamos apenas logar e não atualizar se o tamanho for incompatível.
            return

        print(f"Processo {self.process_id}: Antes da atualização com {received_clock_list}, relógio local é {self.clock}")
        for i in range(self.num_processes):
            self.clock[i] = max(self.clock[i], received_clock_list[i])

        # Após a fusão com o relógio recebido, o evento de "receber e processar a mensagem"
        # também é um evento do processo local, então incrementamos nosso próprio contador.
        self.increment() # Isso também imprimirá o estado final do relógio após a atualização completa.
        # Se não quiséssemos que o print de increment() aparecesse aqui, poderíamos fazer:
        # self.clock[self.process_id] += 1
        # print(f"Processo {self.process_id}: Relógio atualizado para {self.clock}")


    def get_clock_list(self) -> list[int]:
        """
        Retorna uma cópia da lista de inteiros do relógio vetorial atual.
        É uma cópia para evitar modificações externas acidentais.
        """
        return list(self.clock) # Retorna uma cópia

    def get_clock_proto(self) -> VectorClock:
        """
        Retorna o relógio vetorial atual no formato da mensagem Protobuf (VectorClock).
        Isso é útil para anexar o relógio a uma ChatMessage antes de enviá-la.
        """
        vc_proto = VectorClock()
        # O campo 'clock' em VectorClock é um 'repeated int32'.
        # Podemos estendê-lo com nossa lista de relógios.
        vc_proto.clock.extend(self.clock)
        return vc_proto

    def __str__(self):
        return f"Processo {self.process_id} Clock: {self.clock}"