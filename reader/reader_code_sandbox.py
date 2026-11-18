"""在独立命名空间运行真实编译器；不给学生代码网络或宿主材料。"""
import json,subprocess,sys,time
bwrap,folder=sys.argv[1:]
base=[bwrap,'--unshare-all','--die-with-parent','--new-session','--ro-bind','/usr','/usr','--symlink','usr/bin','/bin','--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--bind',folder,'/work','--chdir','/work','--clearenv','--setenv','PATH','/usr/bin','--setenv','LC_ALL','C','--']
limits=['/usr/bin/prlimit','--as=536870912','--cpu=5','--fsize=1048576','--nofile=64','--']
start=time.perf_counter();compile=subprocess.run(base+limits+['/usr/bin/iverilog','-g2012','-s','tb','-o','simulation','solution.sv','tb.sv'],capture_output=True,text=True,timeout=8);compile_seconds=round(time.perf_counter()-start,4)
simulation=None;simulation_seconds=0
if not compile.returncode:
 start=time.perf_counter();simulation=subprocess.run(base+limits+['/usr/bin/vvp','simulation'],capture_output=True,text=True,timeout=8);simulation_seconds=round(time.perf_counter()-start,4)
print(json.dumps({'compile_returncode':compile.returncode,'compile_stdout':compile.stdout[:8000],'compile_stderr':compile.stderr[:8000],'compile_seconds':compile_seconds,'simulation_returncode':simulation.returncode if simulation else None,'simulation_stderr':simulation.stderr[:8000]if simulation else'','simulation_seconds':simulation_seconds,'stdout':simulation.stdout[:20000]if simulation else''}))
