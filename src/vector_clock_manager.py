from chat_pb2 import VectorClock

class VectorClockManager:
    def __init__(self, process_id: int, num_processes: int):
        if not (0 <= process_id < num_processes):
            raise ValueError("process_id deve estar entre 0 e num_processes - 1")

        self.process_id = process_id
        self.clock = [0] * num_processes
        print(f"Processo {self.process_id}: Relógio inicializado como {self.clock}")
    	
     #aumenta o relogio 
    def increment(self):
        self.clock[self.process_id] += 1
        print(f"Processo {self.process_id}: Relógio incrementado para {self.clock}")

    #atualia o relogio
    def update(self, received_clock_list: list[int]):
        if len(received_clock_list) > len(self.clock):
            diff = len(received_clock_list) - len(self.clock)
            self.clock.extend([0] * diff)
            print(f"Processo {self.process_id}: Relógio expandido para {self.clock}")

        elif len(received_clock_list) < len(self.clock):
            print(f"Processo {self.process_id}: Aviso - relógio recebido menor que esperado. Esperado: {len(self.clock)}, Recebido: {len(received_clock_list)}")
            return

        print(f"Processo {self.process_id}: Antes da atualização com {received_clock_list}, relógio local é {self.clock}")
        for i in range(len(received_clock_list)):
            self.clock[i] = max(self.clock[i], received_clock_list[i])

        self.increment()

    def get_clock_list(self) -> list[int]:
        return list(self.clock)

    def get_clock_proto(self) -> VectorClock:
        vc_proto = VectorClock()
        vc_proto.clock.extend(self.clock)
        return vc_proto

    def happened_before(self, other_clock: list[int]) -> bool:
        """Verifica se o relógio local ocorreu antes do outro (ordenação causal)."""
        
        if len(other_clock) != len(self.clock):
            raise ValueError("Relógios incompatíveis para comparação")

        return all(x <= y for x, y in zip(self.clock, other_clock)) and self.clock != other_clock

    def merge_with_max(self, other_clock: list[int]):
        """Atualiza o relógio local com o máximo, mas **sem incrementar**."""
        if len(other_clock) > len(self.clock):
            self.clock.extend([0] * (len(other_clock) - len(self.clock)))

        for i in range(len(other_clock)):
            self.clock[i] = max(self.clock[i], other_clock[i])

    def __str__(self):
        return f"Processo {self.process_id} Clock: {self.clock}"
