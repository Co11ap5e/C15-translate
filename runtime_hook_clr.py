"""打包成 exe 后的 pythonnet 引导（PyInstaller runtime hook）。

.net Framework 那条路在冻结进程里走不通：clr_loader 的 ClrLoader 解析不了
随包的 Python.Runtime.dll，报 "Failed to resolve Python.Runtime.Loader.Initialize"。
改用系统里的 .NET + CoreCLR，并把三件事说清楚：
用哪个 runtime 配置、python313.dll 在哪、从哪找原生库。

必须在 `import webview`（它会 import pythonnet）之前执行完，
所以做成 runtime hook，而不是写在 app.py 里。
"""
import os
import sys

# os.add_dll_directory 返回的句柄必须留着，丢了目录就失效
_HANDLES = []


def _log(lines):
    """启动过程写个小日志，出问题不用重打包就能看到。"""
    try:
        path = os.path.join(os.environ.get("TEMP", "."), "local-translate-clr.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n----\n")
    except Exception:
        pass
    for ln in lines:
        print("[clr]", ln, flush=True)


def _boot():
    if not getattr(sys, "frozen", False):
        return

    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    notes = ["base=%s" % base]

    cfg = os.path.join(base, "desktop.runtimeconfig.json")
    if os.path.isfile(cfg):
        os.environ["PYTHONNET_RUNTIME"] = "coreclr"
        os.environ["PYTHONNET_CORECLR_RUNTIME_CONFIG"] = cfg
    notes.append("runtimeconfig=%s exists=%s" % (cfg, os.path.isfile(cfg)))

    dll = "python%d%d.dll" % (sys.version_info[0], sys.version_info[1])
    for cand in (os.path.join(base, dll), os.path.join(exe_dir, dll)):
        if os.path.isfile(cand):
            os.environ["PYTHONNET_PYDLL"] = cand
            notes.append("pydll=%s" % cand)
            break
    else:
        notes.append("pydll=没找到 %s" % dll)

    if hasattr(os, "add_dll_directory"):
        for path in (base, exe_dir):
            if os.path.isdir(path):
                try:
                    _HANDLES.append(os.add_dll_directory(path))
                except OSError:
                    pass

    t0 = __import__("time").time()
    try:
        import clr  # noqa: F401
        import System

        notes.append("import clr ok: %s (%.1fs)" % (System.Environment.Version, __import__("time").time() - t0))

        # pythonnet 的 import 钩子在 CoreCLR 下按名字找不到框架程序集
        # （报 ModuleNotFoundError: No module named 'System.Windows.Forms'），
        # 先显式装一遍，后面 webview 再 import 就能拿到。
        for name in ("System.Windows.Forms", "System.Drawing",
                     "System.Drawing.Common", "Microsoft.Win32.SystemEvents"):
            try:
                System.Reflection.Assembly.Load(name)
                notes.append("预加载 %s ok" % name)
            except Exception as e:
                notes.append("预加载 %s 失败：%s" % (name, str(e)[:90]))
    except Exception:
        import traceback

        notes.append("import clr 失败:\n" + traceback.format_exc())

    try:
        import System.Windows.Forms  # noqa: F401

        notes.append("System.Windows.Forms ok")
    except Exception:
        import traceback

        notes.append("System.Windows.Forms 失败:\n" + traceback.format_exc())

    lib = os.path.join(base, "webview", "lib", "WebView2Loader.dll")
    notes.append("WebView2Loader=%s exists=%s" % (lib, os.path.isfile(lib)))
    _log(notes)


_boot()
