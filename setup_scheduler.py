"""
setup_scheduler.py
------------------
Registers daily_reflection.py as a Windows Task Scheduler task.
Run this ONCE from an Administrator terminal:

    python setup_scheduler.py

What it creates:
  Task name : TiffDailyReflection
  Schedule  : Every day at midnight (00:00)
  Action    : python daily_reflection.py  (using this project's Python + directory)

To remove the task later:
    schtasks /Delete /TN TiffDailyReflection /F
"""

import subprocess
import sys
import os

TASK_NAME = "TiffDailyReflection"
PYTHON    = sys.executable
SCRIPT    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_reflection.py")
WORKDIR   = os.path.dirname(os.path.abspath(__file__))


def register():
    cmd = [
        "schtasks", "/Create", "/F",
        "/TN",  TASK_NAME,
        "/TR",  f'"{PYTHON}" "{SCRIPT}"',
        "/SC",  "DAILY",
        "/ST",  "00:00",
        "/SD",  "01/01/2026",
        "/RL",  "HIGHEST",
        "/RU",  "SYSTEM",
    ]

    print(f"Registering task: {TASK_NAME}")
    print(f"Python  : {PYTHON}")
    print(f"Script  : {SCRIPT}")
    print(f"Schedule: daily at 00:00\n")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print("Task registered successfully ✓")
        print("Tiff will reflect on her day every night at midnight.")
        print(f"\nTo verify:  schtasks /Query /TN {TASK_NAME}")
        print(f"To run now: schtasks /Run /TN {TASK_NAME}")
        print(f"To remove:  schtasks /Delete /TN {TASK_NAME} /F")
    else:
        print("Failed to register task.")
        print(result.stderr)
        print("\nMake sure you're running this from an Administrator terminal.")
        sys.exit(1)


if __name__ == "__main__":
    register()
