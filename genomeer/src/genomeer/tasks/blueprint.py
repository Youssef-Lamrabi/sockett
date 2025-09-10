from abc import ABC, abstractmethod

class TaskProfil(ABC):
    def __init__(self):
        raise NotImplementedError

    @abstractmethod
    def get_example(self):
        raise NotImplementedError

    @abstractmethod
    def get_iterator(self):
        raise NotImplementedError

    @abstractmethod
    def evaluate(self):
        raise NotImplementedError

    @abstractmethod
    def output_class(self):
        raise NotImplementedError

    @abstractmethod
    def get_prompt_from_input(self, input):
        return self.get_example(input)["prompt"]