import os
import json
import textwrap
import pickle
from concurrent.futures import ThreadPoolExecutor

from openai import OpenAI
from dotenv import load_dotenv
from ddgs import DDGS
import requests

from SQL_dataset import benchmark

load_dotenv()

MODEL = "gpt-4o-mini"

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

class Scraper:
    def __init__(self, task_class, weak_areas: str = "", existing_knowledge: str = ""):
        self.task_class = task_class
        self.weak_areas = weak_areas
        self.existing_knowledge = existing_knowledge

    def _generate_search_queries(self):
        weak_areas_section = textwrap.dedent(f"""
                The agent failed on these specific tasks:
                {self.weak_areas}

                Extract the exact constructs, operators, and patterns that appear in these tasks.
                Generate queries that target those specific constructs directly — not general overviews.
                """).strip() if self.weak_areas else ""

        existing_knowledge_section = textwrap.dedent(f"""
                The agent already knows the following — do NOT generate queries that would return similar information:
                {self.existing_knowledge[:3000]}
                """).strip() if self.existing_knowledge else ""

        response = client.chat.completions.create(
            model=MODEL,
            messages=[{  # type: ignore
                "role": "user",
                "content": f"""
                Generate search queries to find expert-level knowledge about {self.task_class}.

                Generate no more than 5 queries.
                Cover different angles: tools, mistakes, advanced techniques, real-world cases.
                {weak_areas_section}
                {existing_knowledge_section}
                Rules:
                - Each query must target a distinct aspect (no overlaps)
                - Write queries as a practitioner would search, not a student
                  Good: specific problem + context + technique
                  Bad: "best practices for X" or "introduction to X"
                - Return only the queries, one per line, no markdown, no numbering, nothing else
                """
            }]
        )
        queries = response.choices[0].message.content.strip().split("\n")
        return [q.strip() for q in queries if q.strip()]

    def _web_search(self, query):
        with DDGS() as ddgs:
            results = ddgs.text(query, max_results=2)
            return [r["href"] for r in results]

    def _fetch_page(self, url):
        try:
            response = requests.get(f"https://r.jina.ai/{url}", timeout=10)
            return response.text[:10000]
        except Exception:
            return ""

    def summarize(self, raw_texts):
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{  # type: ignore
                "role": "user",
                "content": f"""
                You are curating knowledge for an AI agent that solves {self.task_class} tasks.

                From the research material below, extract only non-obvious, high-value insights.
                Focus on what separates expert practitioners from beginners:
                - Concrete rules the agent can apply directly ("if X then do Y")
                - Non-obvious patterns experts use but beginners miss
                - Real failure modes and how to detect them
                - Subtle semantic differences between similar constructs

                If a piece of material is not directly relevant to {self.task_class} — ignore it completely.

                Structure the output in whatever way best fits the {self.task_class} domain.
                Be specific. Use examples. Avoid platitudes.

                Material:
                {' '.join(raw_texts)}
                """
            }]
        )
        return response.choices[0].message.content

    def scrape(self):
        print("generating queries...")
        queries = self._generate_search_queries()
        for i, q in enumerate(queries, 1):
            print(f"  {i}. {q}")

        print("fetching pages...")
        raw_texts = []
        for query in queries:
            urls = self._web_search(query)
            for url in urls:
                print(f"  {url}")
                text = self._fetch_page(url)
                if text:
                    raw_texts.append(text)
        total_chars = sum(len(t) for t in raw_texts)
        print(f"{len(raw_texts)} pages, {total_chars:,} chars")

        print("Summarizing...")
        return self.summarize(raw_texts)


class StemAgent:
    def __init__(self, task_class):
        self.task_class = task_class
        self.prompt = "You are a general assistant. Answer questions as best you can."
        self.knowledge_base = ""
        self.score_history = []
        self.weak_areas = ""
        self.iteration_count = 0
        self.workflow: list[str] = []

    def scrape(self):
        scraper = Scraper(self.task_class, self.weak_areas, self.knowledge_base)
        insights = scraper.scrape()
        self.knowledge_base += "\n" + insights
        return insights

    def update_prompt(self):
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{ # type: ignore
                "role": "user",
                "content": f"""
                You are creating a system prompt for a specialist {self.task_class} agent.

                Current prompt:
                {self.prompt}

                Knowledge accumulated:
                {self.knowledge_base}

                Rewrite the system prompt incorporating all knowledge.
                Make it concise, structured, and actionable.
                Return only the prompt, nothing else.
                """
            }]
        )
        self.prompt = response.choices[0].message.content
        return self.prompt

    def update_workflow(self):
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{ # type: ignore
                "role": "user",
                "content": f"""
                You are designing a step-by-step reasoning workflow for a {self.task_class} agent.

                Based on the knowledge below, define a sequence of concrete checking steps
                the agent should follow when solving a task. Each step should be a focused
                instruction that builds on the previous one.

                Knowledge:
                {self.knowledge_base}

                Generate no more than 5 steps.
                Return only the steps, one per line, no numbering, no markdown, nothing else.
                """
            }]
        )
        lines = response.choices[0].message.content.strip().split("\n")
        self.workflow = [s.strip() for s in lines if s.strip()]

    def execute(self, task: str) -> str:
        if not self.workflow:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[ # type: ignore
                    {"role": "system", "content": self.prompt},
                    {"role": "user", "content": task}
                ]
            )
            return response.choices[0].message.content

        steps = "\n".join(f"{i+1}. {s}" for i, s in enumerate(self.workflow))
        response = client.chat.completions.create(
            model=MODEL,
            messages=[ # type: ignore
                {"role": "system", "content": self.prompt},
                {"role": "user", "content": f"Follow these steps and show your reasoning for each:\n{steps}\n\nTask:\n{task}"}
            ]
        )
        return response.choices[0].message.content

    def evaluate(self, dataset: list[dict], log_path: str = "eval_log.json") -> float:
        def process(item):
            response = self.execute(item["task"])
            solution = item["solution"]
            solution_list = "\n".join(f"{i+1}. {s}" for i, s in enumerate(solution))
            judge = client.chat.completions.create(
                model=MODEL,
                temperature=0,
                messages=[{ # type: ignore
                    "role": "user",
                    "content": f"""
                    Did the response identify each of the following?

                    {solution_list}

                    Response:
                    {response}

                    For each item, answer yes or no. Return only a list like:
                    1. yes
                    2. no
                    """
                }]
            )
            judge_output = judge.choices[0].message.content.strip()
            lines = judge_output.lower().splitlines()
            verdicts = [{"bug": solution[i], "found": "yes" in (lines[i] if i < len(lines) else "")}
                        for i in range(len(solution))]
            return {"iteration": self.iteration_count, "task": item["task"],
                    "response": response, "judge_raw": judge_output, "verdicts": verdicts}

        with ThreadPoolExecutor(max_workers=10) as executor:
            log_entries = list(executor.map(process, dataset))

        total = 0
        found = 0
        missed = []
        for entry in log_entries:
            for v in entry["verdicts"]:
                total += 1
                if v["found"]:
                    found += 1
            if not all(v["found"] for v in entry["verdicts"]):
                missed.append(entry["task"])

        try:
            with open(log_path, "r") as f:
                existing = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            existing = []
        with open(log_path, "w") as f:
            json.dump(existing + log_entries, f, indent=2, ensure_ascii=False)

        if missed:
            cases = "\n\n---\n\n".join(missed)
            self.weak_areas = f"The agent failed to solve the following tasks:\n\n{cases}"
        else:
            self.weak_areas = ""
        return found / total if total > 0 else 0.0

    def is_improving(self, patience: int = 3) -> bool:
        if len(self.score_history) < patience:
            return True
        recent = self.score_history[-patience:]
        return recent[-1] > recent[0]

    def save(self, path: str = "checkpoint.pkl"):
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @classmethod
    def load(cls, path: str = "checkpoint.pkl") -> "StemAgent":
        with open(path, "rb") as f:
            return pickle.load(f)

    def evolve(self, dataset: list[dict], max_iterations: int = 10):
        print(f"Evolution: {self.task_class}")

        score = self.evaluate(dataset)
        best_score = score
        print(f"[before] score: {score:.2f}")

        for _ in range(max_iterations):
            self.iteration_count += 1
            print(f"\n{"="*55}\n{" "*20}ITERATION {self.iteration_count}{" "*20}\n{"="*55}")

            self.scrape()
            self.update_prompt()
            self.update_workflow()
            print(f"======= Prompt =======\n{self.prompt}\n{"-"*55}")
            print("======= Workflow =======\n" + "\n".join(f"  {s}" for s in self.workflow) + f"\n{"-"*55}")

            score = self.evaluate(dataset)
            self.score_history.append(score)
            print(f"score: {score:.2f}")

            self.save()
            if score > best_score:
                best_score = score
                self.save("checkpoint_best.pkl")

            if score == 1.0 or not self.is_improving():
                print("early stopping")
                break

        if self.score_history:
            print(f"\n[done] iterations: {self.iteration_count}, final score: {self.score_history[-1]:.2f}, best score: {best_score:.2f}")


if __name__ == "__main__":
    if os.path.exists("checkpoint.pkl"):
        agent = StemAgent.load()
        print("Loaded from checkpoint.")
    else:
        agent = StemAgent(task_class="SQL Correctness Bug Detection")

    agent.evolve(benchmark)
