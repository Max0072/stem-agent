import os
import json
from openai import OpenAI
from SQL_dataset import benchmark

from dotenv import load_dotenv
from ddgs import DDGS
import requests

import textwrap
import pickle
import re

load_dotenv()

MODEL = "gpt-4o-mini"
# MODEL = "claude-sonnet-4-6"
# MODEL = "claude-haiku-4-5-20251001"

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
# client = OpenAI(
#     api_key=os.environ.get("ANTHROPIC_API_KEY"),
#     base_url="https://api.anthropic.com/v1/"
# )

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
        """Search DuckDuckGo, return list of URLs."""
        with DDGS() as ddgs:
            results = ddgs.text(query, max_results=2)
            return [r["href"] for r in results]

    def _fetch_page(self, url):
        """Fetch full page content via Jina Reader."""
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

                Structure the output in whatever way best fits the {self.task_class} domain.
                Be specific. Use examples. Avoid platitudes.

                Material:
                {' '.join(raw_texts)}
                """
            }]
        )
        return response.choices[0].message.content

    def scrape(self):
        """Research domain, extract actionable insights."""

        # ====== Generating queries ======
        print("Generating queries...")
        queries = self._generate_search_queries()
        print(queries)

        # ===== Collecting raw texts =====
        print("Fetching pages, collecting raw texts...")
        raw_texts = []
        for query in queries:
            urls = self._web_search(query)
            for url in urls:
                print(url)
                text = self._fetch_page(url)
                if text:
                    raw_texts.append(text)
        total_chars = sum(len(t) for t in raw_texts)
        print(f"Scraped {len(raw_texts)} pages, {total_chars:,} chars")

        # ========= Summarizing ==========
        print("Summarizing...")
        summary = self.summarize(raw_texts)

        return summary


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
        prompt = response.choices[0].message.content

        self.prompt = prompt
        return prompt

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
        self.workflow = [re.sub(r'^\d+[\.\)]\s*', '', s.strip()) for s in lines if s.strip()]

    def execute(self, task: str) -> str:
        """Solve a task using current prompt and workflow."""
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

    def evaluate(self, benchmark: list[dict], log_path: str = "eval_log.json") -> float:
        """Score current performance. Updates self.weak_areas with failed cases."""
        total = 0
        found = 0
        missed = []
        log_entries = []

        for item in benchmark:
            response = self.execute(item["task"])
            solution = item["solution"]
            total += len(solution)

            solution_list = "\n".join(f"{i+1}. {s}" for i, s in enumerate(solution))
            judge = client.chat.completions.create(
                model=MODEL,
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
            verdicts = []
            missed_this = False
            for i in range(len(solution)):
                answer = lines[i] if i < len(lines) else ""
                hit = "yes" in answer
                verdicts.append({"bug": solution[i], "found": hit})
                if hit:
                    found += 1
                else:
                    missed_this = True

            log_entries.append({
                "iteration": self.iteration_count,
                "task": item["task"],
                "response": response,
                "judge_raw": judge_output,
                "verdicts": verdicts,
            })

            if missed_this:
                missed.append(item["task"])

        # Append to log file
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
        """Early stopping: return False if score hasn't improved in `patience` iterations."""
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

    def evolve(self, benchmark: list[dict], max_iterations: int = 10):
        print(f"Starting evolution: task_class='{self.task_class}', max_iterations={max_iterations}")

        # ========= Initial score =========
        score = self.evaluate(benchmark)
        print(f"[before] score: {score:.2f}")
        if self.weak_areas:
            print(f"weak areas:\n{self.weak_areas}\n")

        # ============= Loop ==============
        for i in range(max_iterations):
            self.iteration_count += 1
            print(f"\n--- iteration {self.iteration_count} ---")

            self.scrape()                       # Scraping
            self.update_prompt()                # Updating prompt
            self.update_workflow()              # Updating workflow
            print(f"prompt:\n{self.prompt}\n")
            print(f"workflow:\n" + "\n".join(f"  {s}" for i, s in enumerate(self.workflow)) + "\n")

            score = self.evaluate(benchmark)    # Evaluation
            self.score_history.append(score)
            print(f"score: {score:.2f}")
            if self.weak_areas:
                print(f"weak areas:\n{self.weak_areas}\n")

            self.save()                         # Save state

            if score == 1.0 or not self.is_improving():
                print("early stopping")
                break

        if self.score_history:
            print(f"\n[done] iterations: {self.iteration_count}, final score: {self.score_history[-1]:.2f}")


if __name__ == "__main__":
    if os.path.exists("checkpoint.pkl"):
        agent = StemAgent.load()
        print("Loaded from checkpoint.")
    else:
        agent = StemAgent(task_class="SQL Correctness Bug Detection")

    agent.evolve(benchmark)
