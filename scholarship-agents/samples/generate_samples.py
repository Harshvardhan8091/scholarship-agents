"""
Generates realistic sample files for testing the AI Scholarship Finder multi-agent system.
Produces:
1. sample_resume.pdf
2. sample_marksheet.pdf
3. sample_marksheet.txt
"""

from pathlib import Path
from fpdf import FPDF

SAMPLES_DIR = Path(__file__).resolve().parent
SAMPLES_DIR.mkdir(parents=True, exist_ok=True)


def create_sample_resume_pdf(output_path: Path) -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Header
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "Alex Rivera", ln=True, align="C")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, "Email: alex.rivera.cs@gmail.com | Phone: +1-555-019-2834 | GitHub: alexrivera-dev", ln=True, align="C")
    pdf.cell(0, 5, "Target Countries for Graduate Study: Germany, Switzerland, United Kingdom, United States", ln=True, align="C")
    pdf.ln(5)

    # Education
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, "EDUCATION", ln=True)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Bachelor of Technology in Computer Science & Engineering", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, "Institute of Technology & Sciences (2021 - 2025)", ln=True)
    pdf.cell(0, 5, "Cumulative Grade Point Average (CGPA): 8.92 / 10.0 (Top 5% of graduating class)", ln=True)
    pdf.ln(4)

    # Technical Skills
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, "TECHNICAL SKILLS", ln=True)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)

    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, "Languages & Runtimes: Python, C++, Go, TypeScript, SQL\n"
                         "Frameworks & Tools: FastAPI, PyTorch, LangGraph, Docker, Kubernetes, PostgreSQL\n"
                         "Core Competencies: Distributed Systems, Machine Learning Systems, High-Performance Computing")
    pdf.ln(4)

    # Projects
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, "SELECTED RESEARCH & ENGINEERING PROJECTS", ln=True)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "1. Edge-AI Inference Accelerator (Python, C++, TensorRT)", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, "- Designed an open-source low-latency quantized neural inference pipeline for resource-constrained edge hardware.\n"
                         "- Reduced memory footprint by 42% and achieved 3.2x throughput increase on Jetson platforms.\n"
                         "- Published codebase with 140+ GitHub stars and complete documentation.")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "2. Distributed Vector Index for Semantic Retrieval (Go, gRPC, Raft)", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, "- Engineered a fault-tolerant vector clustering service with HNSW graph indexing and Raft consensus.\n"
                         "- Sustained 4,500 queries per second with p99 latency under 12ms across a 4-node cluster.")
    pdf.ln(4)

    # Honors & Achievements
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, "ACADEMIC HONORS & ACHIEVEMENTS", ln=True)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)

    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5, "- Winner, National Inter-University AI Innovation Hackathon (1st place among 120 teams, 2024)\n"
                         "- Dean's Merit Scholarship Award for Academic Distinction (consecutive 3 years: 2022, 2023, 2024)\n"
                         "- Author of peer-reviewed workshop paper on Edge Intelligence at IEEE Student Symposium 2024")

    pdf.output(str(output_path))
    print(f"Created sample resume PDF: {output_path}")


def create_sample_marksheet_pdf(output_path: Path) -> None:
    pdf = FPDF()
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "OFFICIAL ACADEMIC TRANSCRIPT / MARKSHEET", ln=True, align="C")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, "Candidate Name: Alex Rivera | Student ID: CS-2021-8842", ln=True, align="C")
    pdf.cell(0, 5, "Program: Bachelor of Technology (Honors) in Computer Science & Engineering", ln=True, align="C")
    pdf.ln(5)

    # Table Header
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(25, 7, "Code", 1, 0, "C")
    pdf.cell(95, 7, "Course Title", 1, 0, "L")
    pdf.cell(25, 7, "Credits", 1, 0, "C")
    pdf.cell(25, 7, "Grade", 1, 0, "C")
    pdf.cell(20, 7, "Point", 1, 1, "C")

    courses = [
        ("CS-301", "Design and Analysis of Algorithms", "4.0", "A+", "10.0"),
        ("CS-302", "Operating Systems & Concurrency", "4.0", "A", "9.0"),
        ("CS-303", "Machine Learning & Pattern Recognition", "4.0", "A+", "10.0"),
        ("CS-304", "Database Management & Distributed Storage", "3.0", "A", "9.0"),
        ("CS-305", "Computer Networks & Protocols", "3.0", "A-", "8.0"),
        ("CS-401", "Deep Learning Systems Architecture", "4.0", "A+", "10.0"),
        ("CS-402", "Cloud Computing & Microservices", "3.0", "A", "9.0"),
        ("MA-201", "Linear Algebra & Vector Calculus", "4.0", "A", "9.0"),
        ("MA-202", "Probability, Statistics & Random Processes", "4.0", "A", "9.0"),
    ]

    pdf.set_font("Helvetica", "", 9)
    for code, title, credits, grade, point in courses:
        pdf.cell(25, 6, code, 1, 0, "C")
        pdf.cell(95, 6, title, 1, 0, "L")
        pdf.cell(25, 6, credits, 1, 0, "C")
        pdf.cell(25, 6, grade, 1, 0, "C")
        pdf.cell(20, 6, point, 1, 1, "C")

    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Summary Performance Evaluation:", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, "Total Cumulative Credits Earned: 132.0", ln=True)
    pdf.cell(0, 5, "Cumulative Grade Point Average (CGPA): 8.92 / 10.00", ln=True)
    pdf.cell(0, 5, "Equivalent Percentage: 89.2% | Academic Standing: First Class with Distinction", ln=True)

    pdf.output(str(output_path))
    print(f"Created sample marksheet PDF: {output_path}")


def create_sample_marksheet_txt(output_path: Path) -> None:
    content = """========================================================================
ACADEMIC TRANSCRIPT RECORD (TEXT FORMAT)
========================================================================
Student Name: Alex Rivera
Registration No: CS-2021-8842
Degree: Bachelor of Technology in Computer Science & Engineering
Specialization: Systems and Artificial Intelligence

SEMESTER WISE PERFORMANCE SUMMARY:
Semester I:   GPA 8.70 / 10.0
Semester II:  GPA 8.85 / 10.0
Semester III: GPA 8.90 / 10.0
Semester IV:  GPA 9.00 / 10.0
Semester V:   GPA 8.95 / 10.0
Semester VI:  GPA 9.10 / 10.0

CUMULATIVE CGPA: 8.92 / 10.00
Classification: First Class with Distinction (Honors)
Core Disciplines: Algorithms (A+), Operating Systems (A), Machine Learning (A+)
========================================================================
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Created sample marksheet TXT: {output_path}")


if __name__ == "__main__":
    create_sample_resume_pdf(SAMPLES_DIR / "sample_resume.pdf")
    create_sample_marksheet_pdf(SAMPLES_DIR / "sample_marksheet.pdf")
    create_sample_marksheet_txt(SAMPLES_DIR / "sample_marksheet.txt")
