"""
LaTeX resume compiler module.

Responsibilities:
  - Accept a data dict (name, experience, skills, etc.) and render it into
    the Jinja2-enabled LaTeX template at modules/resume/templates/resume.tex
  - Write the rendered .tex to a temp directory
  - Shell out to pdflatex (or configured compiler) to produce a PDF
  - Return the path to the compiled PDF
  - Log compilation errors to the database

Usage:
    from modules.resume.compiler import ResumeCompiler

    compiler = ResumeCompiler(config)
    pdf_path = compiler.compile(applicant_data, output_dir="output/resumes")
"""

import os
import subprocess
import tempfile
from jinja2 import Environment, FileSystemLoader


class ResumeCompiler:
    def __init__(self, config: dict):
        # config comes from app.config["JOB_BOT"]["resume"]
        self.template_path = config.get("template", "modules/resume/templates/resume.tex")
        self.output_dir = config.get("output_dir", "output/resumes")
        self.compiler = config.get("latex_compiler", "pdflatex")

    def compile(self, data: dict, output_dir: str | None = None) -> str:
        """
        Render the LaTeX template with data and compile to PDF.

        Returns the absolute path of the generated PDF.
        Raises RuntimeError if pdflatex exits with a non-zero code.
        """
        # TODO: render template via Jinja2, write .tex, run self.compiler, return pdf path
        raise NotImplementedError

    def _render_template(self, data: dict) -> str:
        """Return the rendered LaTeX source string."""
        template_dir = os.path.dirname(os.path.abspath(self.template_path))
        env = Environment(
            loader=FileSystemLoader(template_dir),
            block_start_string="((*",
            block_end_string="*))",
            variable_start_string="(((",
            variable_end_string=")))",
            comment_start_string="((#",
            comment_end_string="#))",
        )
        # Use alternate delimiters so Jinja2 doesn't conflict with LaTeX braces
        template = env.get_template(os.path.basename(self.template_path))
        return template.render(**data)

    def _run_latex(self, tex_path: str, out_dir: str) -> str:
        """Invoke pdflatex and return the resulting PDF path."""
        result = subprocess.run(
            [self.compiler, "-interaction=nonstopmode", "-output-directory", out_dir, tex_path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"LaTeX compilation failed:\n{result.stdout}\n{result.stderr}")
        base = os.path.splitext(os.path.basename(tex_path))[0]
        return os.path.join(out_dir, f"{base}.pdf")
