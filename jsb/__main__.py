import os
import sys

# Options that work with any command, anywhere on the command line. They have to be set before
# anything imports jsb.config, which reads them.
#   --profile NAME         run as another candidate (data in profiles/NAME/)
#   --min-pay 200K         only jobs whose posted base pay reaches above this (0 = off)
#   --perm [MONTHS]        skip companies with layoffs in the last MONTHS (default 6)
#   --no-perm              turn the layoff (PERM) filter off
VALUED = {"--profile": "JSB_PROFILE", "--min-pay": "JSB_MIN_PAY"}
args, i = sys.argv[1:], 0
rest = []
while i < len(args):
    a = args[i]
    key, eq, val = a.partition("=")
    if key in VALUED:
        if not eq:
            if i + 1 >= len(args):
                sys.exit(f"{key} needs a value")
            val, i = args[i + 1], i + 1
        os.environ[VALUED[key]] = val
    elif key == "--perm":
        if not eq and i + 1 < len(args) and args[i + 1].isdigit():
            val, i = args[i + 1], i + 1
        os.environ["JSB_PERM_MONTHS"] = val or "6"
    elif a == "--no-perm":
        os.environ["JSB_PERM_MONTHS"] = "0"
    else:
        rest.append(a)
    i += 1
sys.argv[1:] = rest

from .cli import main  # noqa: E402

main()
