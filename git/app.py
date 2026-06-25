import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from scripts.carbon_study_app import main

main()
