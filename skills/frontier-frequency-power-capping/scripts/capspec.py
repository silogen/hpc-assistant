#!/usr/bin/env python3
"""Parse and validate Frontier GPU cap requests (rules of /etc/slurm/lua.d/gpu-srange.lua
and gpu-power-cap.lua; a max sclk above 1700 MHz is refused unless allow_above_max).

CLI: capspec.py {label|expect|flags|header} SRANGE POWER
SRANGE is "default", "MAX" or "MIN-MAX" (MHz); POWER is "default" or watts.
"""
import re
import sys

SCLK_MIN = 500
SCLK_DEFAULT_MAX = 1700
POWER_MIN = 100
POWER_MAX = 600
POWER_DEFAULT = 560
DEFAULT_LABEL = f"s{SCLK_MIN}-{SCLK_DEFAULT_MAX}_p{POWER_DEFAULT}"


class CapError(ValueError):
    pass


def _int(text, what):
    text = text.strip()
    if not re.fullmatch(r"\d+", text):
        raise CapError(f"{what} must be a positive integer, got {text!r}")
    return int(text)


def parse_srange(spec, allow_above_max=False):
    """Return (min_mhz, max_mhz) for a --gpu-srange request."""
    spec = (spec or "default").strip()
    if spec.lower() == "default":
        return SCLK_MIN, SCLK_DEFAULT_MAX
    parts = spec.split("-")
    if len(parts) == 1:
        lo, hi = SCLK_MIN, _int(parts[0], "sclk max")
    elif len(parts) == 2:
        lo, hi = _int(parts[0], "sclk min"), _int(parts[1], "sclk max")
    else:
        raise CapError(f"sclk range must be MIN-MAX or MAX, got {spec!r}")
    if lo < SCLK_MIN:
        raise CapError(f"sclk min {lo} MHz is below the plugin minimum {SCLK_MIN} MHz")
    if lo >= hi:
        raise CapError(f"sclk min {lo} must be strictly below max {hi} (the plugin rejects min >= max)")
    if hi > SCLK_DEFAULT_MAX and not allow_above_max:
        raise CapError(f"sclk max {hi} MHz exceeds the MI250X peak {SCLK_DEFAULT_MAX} MHz "
                       "(the plugin only allows this for margin testing)")
    return lo, hi


def parse_power(spec):
    """Return watts for a --gpu-power-cap request (applied per MI250X package)."""
    spec = (str(spec) if spec is not None else "default").strip()
    if spec.lower() == "default":
        return POWER_DEFAULT
    watts = _int(spec, "power cap")
    if not POWER_MIN <= watts <= POWER_MAX:
        raise CapError(f"power cap {watts} W is outside the plugin range {POWER_MIN}-{POWER_MAX} W")
    return watts


def label(lo, hi, watts):
    return f"s{lo}-{hi}_p{watts}"


def parse_label(text):
    m = re.fullmatch(r"s(\d+)-(\d+)_p(\d+)", text)
    if not m:
        raise CapError(f"not a cap label: {text!r}")
    return tuple(int(x) for x in m.groups())


def sbatch_flags(lo, hi, watts):
    return [f"--gpu-srange={lo}-{hi}", f"--gpu-power-cap={watts}"]


def sbatch_header(lo, hi, watts):
    return [f"#SBATCH --gpu-power-cap={watts}", f"#SBATCH --gpu-srange={lo}-{hi}"]


def main(argv):
    if len(argv) != 4 or argv[1] not in {"label", "expect", "flags", "header"}:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        lo, hi = parse_srange(argv[2])
        watts = parse_power(argv[3])
    except CapError as exc:
        print(f"capspec: {exc}", file=sys.stderr)
        return 2
    if argv[1] == "label":
        print(label(lo, hi, watts))
    elif argv[1] == "expect":
        print(lo, hi, watts)
    elif argv[1] == "flags":
        print(" ".join(sbatch_flags(lo, hi, watts)))
    else:
        print("\n".join(sbatch_header(lo, hi, watts)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
