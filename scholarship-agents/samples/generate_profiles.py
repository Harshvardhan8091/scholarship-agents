"""
Generate three fictional student profiles (Item 7):
  1. CS student with high CGPA  (already exists as sample_resume.pdf)
  2. Mechanical Engineering student with lower CGPA
  3. Commerce / Business student

Produces resume + marksheet PDFs for each in samples/.
"""
from pathlib import Path
from fpdf import FPDF

SAMPLES_DIR = Path(__file__).resolve().parent
SAMPLES_DIR.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────────
# Profile 2: Mechanical Engineering, lower CGPA
# ──────────────────────────────────────────────────
def create_mech_resume(out: Path) -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "Priya Mehta", ln=True, align="C")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, "Email: priya.mehta.mech@outlook.com | Phone: +91-98765-43210 | GitHub: priyamehta-eng", ln=True, align="C")
    pdf.cell(0, 5, "Target Countries for Graduate Study: Japan, South Korea, Canada", ln=True, align="C")
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, "EDUCATION", ln=True)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Bachelor of Engineering in Mechanical Engineering", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, "National College of Engineering (2020 - 2024)", ln=True)
    pdf.cell(0, 5, "CGPA: 7.10 / 10.0", ln=True)
    pdf.cell(0, 5, "Financial Need: High (requires full funding for graduate study)", ln=True)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, "TECHNICAL SKILLS", ln=True)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5,
        "CAD/CAM: SolidWorks, AutoCAD, CATIA\n"
        "Simulation: ANSYS, MATLAB, Simulink\n"
        "Programming: Python, MATLAB scripting, Arduino\n"
        "Core Areas: Thermodynamics, Fluid Mechanics, Robotics, Manufacturing"
    )
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, "PROJECTS", ln=True)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "1. Solar-Powered Water Desalination Unit", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5,
        "- Designed and tested a compact solar still achieving 72% recovery.\n"
        "- Won 2nd place in national sustainable engineering competition."
    )
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "2. Autonomous Line-Following Robot (Arduino, PID)", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5,
        "- Built a robot capable of navigating complex track layouts using PID control.\n"
        "- Completed an 18-meter track in 32 seconds during inter-college robotics fest."
    )
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, "ACHIEVEMENTS", ln=True)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5,
        "- 2nd Place, National Sustainable Engineering Design Challenge (2023)\n"
        "- Published poster paper at ASME Student Conference on Renewable Energy Systems"
    )

    pdf.output(str(out))
    print(f"Created: {out}")


def create_mech_marksheet(out: Path) -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "ACADEMIC TRANSCRIPT", ln=True, align="C")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, "Candidate: Priya Mehta | ID: ME-2020-4421", ln=True, align="C")
    pdf.cell(0, 5, "Program: B.E. Mechanical Engineering", ln=True, align="C")
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(25, 7, "Code", 1, 0, "C")
    pdf.cell(95, 7, "Course Title", 1, 0, "L")
    pdf.cell(25, 7, "Credits", 1, 0, "C")
    pdf.cell(25, 7, "Grade", 1, 0, "C")
    pdf.cell(20, 7, "Point", 1, 1, "C")

    courses = [
        ("ME-301", "Thermodynamics II", "4.0", "B+", "7.5"),
        ("ME-302", "Fluid Mechanics", "4.0", "B+", "7.5"),
        ("ME-303", "Strength of Materials", "3.0", "A-", "8.0"),
        ("ME-304", "Manufacturing Processes", "3.0", "B", "7.0"),
        ("ME-305", "Machine Design", "4.0", "B+", "7.5"),
        ("ME-401", "Robotics & Automation", "4.0", "A-", "8.0"),
        ("ME-402", "Heat Transfer", "3.0", "B", "7.0"),
        ("MA-201", "Engineering Mathematics III", "4.0", "B", "7.0"),
    ]

    pdf.set_font("Helvetica", "", 9)
    for code, title, credits, grade, point in courses:
        pdf.cell(25, 6, code, 1, 0, "C")
        pdf.cell(95, 6, title, 1, 0, "L")
        pdf.cell(25, 6, credits, 1, 0, "C")
        pdf.cell(25, 6, grade, 1, 0, "C")
        pdf.cell(20, 6, point, 1, 1, "C")

    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Cumulative CGPA: 7.10 / 10.00", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, "Classification: Second Class", ln=True)

    pdf.output(str(out))
    print(f"Created: {out}")


# ──────────────────────────────────────────────────
# Profile 3: Commerce / Business student
# ──────────────────────────────────────────────────
def create_biz_resume(out: Path) -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "Rohan Kapoor", ln=True, align="C")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, "Email: rohan.kapoor.biz@gmail.com | Phone: +91-81234-56789 | LinkedIn: rohankapoor-biz", ln=True, align="C")
    pdf.cell(0, 5, "Target Countries for Graduate Study: United Kingdom, France, Australia", ln=True, align="C")
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, "EDUCATION", ln=True)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Bachelor of Commerce (Honours) in Finance & Accounting", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, "Delhi School of Economics (2021 - 2024)", ln=True)
    pdf.cell(0, 5, "CGPA: 8.25 / 10.0", ln=True)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, "SKILLS", ln=True)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5,
        "Financial Modelling, Valuation, Equity Research, Excel VBA\n"
        "Data Analysis: Tableau, Power BI, SQL\n"
        "Certifications: CFA Level I Passed, Bloomberg Market Concepts"
    )
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, "EXPERIENCE & PROJECTS", ln=True)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "1. Equity Research Intern - Capital First Securities (Summer 2023)", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5,
        "- Authored 4 sector reports covering Indian FMCG and Banking stocks.\n"
        "- Portfolio picks outperformed the Nifty 50 by 6.3% over the quarter."
    )
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "2. Microfinance Impact Study (capstone project)", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5,
        "- Surveyed 200 rural borrowers to assess micro-loan impact on household income.\n"
        "- Findings presented at National Finance Students' Conclave 2024."
    )
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, "ACHIEVEMENTS", ln=True)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 5,
        "- National Merit Scholarship from Government of India (2021-2024)\n"
        "- 1st Place in National Business Case Competition organized by IIM Bangalore\n"
        "- CFA Level I Cleared on first attempt"
    )

    pdf.output(str(out))
    print(f"Created: {out}")


def create_biz_marksheet(out: Path) -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "ACADEMIC TRANSCRIPT", ln=True, align="C")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, "Candidate: Rohan Kapoor | ID: BCOM-2021-1190", ln=True, align="C")
    pdf.cell(0, 5, "Program: B.Com (Hons) Finance & Accounting", ln=True, align="C")
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(25, 7, "Code", 1, 0, "C")
    pdf.cell(95, 7, "Course Title", 1, 0, "L")
    pdf.cell(25, 7, "Credits", 1, 0, "C")
    pdf.cell(25, 7, "Grade", 1, 0, "C")
    pdf.cell(20, 7, "Point", 1, 1, "C")

    courses = [
        ("FIN-301", "Corporate Finance", "4.0", "A", "9.0"),
        ("FIN-302", "Investment Analysis", "4.0", "A-", "8.0"),
        ("ACC-301", "Financial Accounting II", "3.0", "A", "9.0"),
        ("ACC-302", "Cost & Management Accounting", "3.0", "B+", "7.5"),
        ("ECO-301", "Macroeconomics", "3.0", "A-", "8.0"),
        ("MGT-301", "Business Strategy", "3.0", "A", "9.0"),
        ("STA-201", "Business Statistics", "4.0", "B+", "7.5"),
    ]

    pdf.set_font("Helvetica", "", 9)
    for code, title, credits, grade, point in courses:
        pdf.cell(25, 6, code, 1, 0, "C")
        pdf.cell(95, 6, title, 1, 0, "L")
        pdf.cell(25, 6, credits, 1, 0, "C")
        pdf.cell(25, 6, grade, 1, 0, "C")
        pdf.cell(20, 6, point, 1, 1, "C")

    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Cumulative CGPA: 8.25 / 10.00", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, "Classification: First Class", ln=True)

    pdf.output(str(out))
    print(f"Created: {out}")


if __name__ == "__main__":
    # Profile 2: Mechanical Engineering
    create_mech_resume(SAMPLES_DIR / "mech_resume.pdf")
    create_mech_marksheet(SAMPLES_DIR / "mech_marksheet.pdf")
    # Profile 3: Commerce / Business
    create_biz_resume(SAMPLES_DIR / "biz_resume.pdf")
    create_biz_marksheet(SAMPLES_DIR / "biz_marksheet.pdf")
    print("\nAll sample profiles generated successfully!")
