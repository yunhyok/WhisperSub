"""PyInstaller entry point (the package uses relative imports, so it cannot be the script itself)."""
import sys
from whispersub.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
