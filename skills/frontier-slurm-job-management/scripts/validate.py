#!/usr/bin/env python3
"""Offline checks for the frontier-slurm-job-management, frontier-resource-management
and frontier-frequency-power-capping skills. Needs no network, Slurm, or GPU.

Checks:
  - required files exist
  - SKILL.md frontmatter parses and has name/description
  - every relative markdown link resolves to a real file
  - every fenced bash code block in references/*.md parses with `bash -n`
  - templates parse with `bash -n`
  - the array-job manifest logic handles a filename containing a space
"""
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
SKILLS = ["frontier-slurm-job-management", "frontier-resource-management", "frontier-frequency-power-capping"]
FAILS = []


def fail(msg):
    FAILS.append(msg)
    print("FAIL:", msg)


def ok(msg):
    print("OK:", msg)


def check_frontmatter(skill_md: pathlib.Path):
    text = skill_md.read_text()
    if not text.startswith("---"):
        fail(f"{skill_md}: missing leading frontmatter")
        return
    m = re.search(r"\n---\s*\n", text[3:])
    if not m:
        fail(f"{skill_md}: frontmatter not closed")
        return
    fm = text[3 : m.start() + 3]
    if "name:" not in fm or "description:" not in fm:
        fail(f"{skill_md}: frontmatter missing name/description")
    else:
        ok(f"{skill_md}: frontmatter has name/description")


def check_links(md_path: pathlib.Path):
    text = md_path.read_text()
    for m in re.finditer(r"\[[^\]]*\]\(((?!https?://)[^)#]+)[^)]*\)", text):
        target = m.group(1)
        resolved = (md_path.parent / target).resolve()
        if not resolved.exists():
            fail(f"{md_path}: broken relative link {target}")
    ok(f"{md_path}: relative links checked")


def check_bash_blocks(md_path: pathlib.Path):
    text = md_path.read_text()
    blocks = re.findall(r"```bash\n(.*?)```", text, re.S)
    for i, block in enumerate(blocks):
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
            f.write(block)
            path = f.name
        proc = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
        if proc.returncode != 0:
            fail(f"{md_path}: bash block {i} failed syntax check: {proc.stderr.strip()}")
    ok(f"{md_path}: {len(blocks)} bash blocks syntax-checked")


def check_array_manifest_with_space():
    # Run the array-job manifest logic from examples.md on a filename
    # containing a space.
    with tempfile.TemporaryDirectory() as d:
        dpath = pathlib.Path(d)
        f1 = dpath / "input with space.json"
        f1.write_text("{}")
        manifest = dpath / "inputs.txt"
        manifest.write_text(str(f1) + "\n" + "\n".join(str(dpath / f"input{i}.json") for i in range(2, 11)) + "\n")
        for i in range(2, 11):
            (dpath / f"input{i}.json").write_text("{}")
        script = f"""
mapfile -t INPUTS < {manifest}
if (( ${{#INPUTS[@]}} != 10 )); then echo BAD_COUNT; exit 2; fi
DATA="${{INPUTS[0]}}"
test -f "$DATA" && echo GOOD
"""
        proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        if "GOOD" in proc.stdout:
            ok("array manifest logic handles filename with a space")
        else:
            fail(f"array manifest logic failed: {proc.stdout} {proc.stderr}")


def main():
    for skill in SKILLS:
        base = ROOT / "skills" / skill
        if not base.exists():
            fail(f"missing skill directory {base}")
            continue
        skill_md = base / "SKILL.md"
        if not skill_md.exists():
            fail(f"missing {skill_md}")
            continue
        check_frontmatter(skill_md)
        check_links(skill_md)
        for ref in sorted((base / "references").glob("*.md")):
            check_links(ref)
            check_bash_blocks(ref)
        for tmpl in sorted((base / "templates").glob("*")) if (base / "templates").exists() else []:
            proc = subprocess.run(["bash", "-n", str(tmpl)], capture_output=True, text=True)
            if proc.returncode != 0:
                fail(f"{tmpl}: bash -n failed: {proc.stderr.strip()}")
            else:
                ok(f"{tmpl}: bash -n passed")

    check_array_manifest_with_space()

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILURE(S)")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
