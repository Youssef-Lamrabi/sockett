import numpy as np
np.random.seed(42)
from genomeer.tasks.blueprint import TaskProfil

class LabBench(TaskProfil):
    def __init__(self):
        self.prompt = "The following is a biology MCQ.\nQuestion: {q}\nOptions:\n{opts}\n[ANSWER]A[/ANSWER]"
        self._data = [("What is DNA?", ["A. Nucleic acid", "B. Lipid", "C. Protein", "D. Sugar"], "A")]

    def get_example(self, index=None):
        q, opts, ans = self._data[0]
        return {"prompt": self.prompt.format(q=q, opts="\n".join(opts)), "answer": ans}

    def get_iterator(self):
        yield self.get_example(0)

    def evaluate(self, response):
        return {"accuracy": 1.0}

    def output_class(self):
        from pydantic import BaseModel, Field
        class MultipleChoiceOutput(BaseModel):
            choice: str | None = Field(description="Single-letter answer")
        return MultipleChoiceOutput
