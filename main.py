"""Agency Analytics Kit - CLI entry point."""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

from agency_analytics.cli import cli

if __name__ == "__main__":
    sys.exit(cli())
