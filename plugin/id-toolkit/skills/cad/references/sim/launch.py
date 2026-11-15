# Runs a shell command with the FEA conda env, started through headless Blender (blender_render), so it
# works from the chat without a terminal. The command runs as a child shell and keeps going when Blender
# itself is ended (some Macs end every Blender process after ~35 s).
#   blender -b -P <sim>/scripts/launch.py -- <logfile> <command...>
# cwd = the sim folder (parent of scripts/). Env: $IDTK_FEA_ENV or the conda env "idtk-fea".
import sys, subprocess, os, glob
a = sys.argv[sys.argv.index("--") + 1:]
log, cmd = a[0], " ".join(a[1:])
sim = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_bin = os.environ.get("IDTK_FEA_ENV", "")
if not env_bin:
    for base in ["/opt/homebrew/Caskroom/miniforge/base", os.path.expanduser("~/miniforge3"),
                 os.path.expanduser("~/miniconda3"), "/opt/homebrew/anaconda3"]:
        p = os.path.join(base, "envs", "idtk-fea", "bin")
        if os.path.isdir(p): env_bin = p; break
env = dict(os.environ, PATH=env_bin + ":/opt/homebrew/bin:" + os.environ.get("PATH", ""))
with open(log, "w") as f:
    r = subprocess.run(cmd, shell=True, cwd=sim, env=env, stdout=f, stderr=subprocess.STDOUT)
    f.write(f"\n__EXIT {r.returncode}\n")
