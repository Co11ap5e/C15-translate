using System;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

class Host
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr LoadLibraryW(string path);

    [DllImport("kernel32.dll", CharSet = CharSet.Ansi, SetLastError = true)]
    static extern IntPtr GetProcAddress(IntPtr module, string name);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    delegate int PyBytesMain(int argc, IntPtr argv);

    [STAThread]
    static int Main(string[] args)
    {
        string here = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
        string rt = Path.Combine(here, "runtime");
        string dll = Path.Combine(rt, "python313.dll");
        string script = Path.Combine(here, "app.py");

        if (!File.Exists(dll) || !File.Exists(script))
        {
            MessageBox.Show("runtime\\python313.dll 或 app.py 不在这个文件夹里。", "本地翻译");
            return 1;
        }

        Environment.SetEnvironmentVariable("PATH",
            rt + ";" + Path.Combine(rt, "DLLs") + ";" + Environment.GetEnvironmentVariable("PATH"));
        Directory.SetCurrentDirectory(here);

        IntPtr mod = LoadLibraryW(dll);
        if (mod == IntPtr.Zero)
        {
            MessageBox.Show("加载 python313.dll 失败，错误码 " + Marshal.GetLastWin32Error(), "本地翻译");
            return 1;
        }
        IntPtr fn = GetProcAddress(mod, "Py_BytesMain");
        if (fn == IntPtr.Zero)
        {
            MessageBox.Show("python313.dll 里没有 Py_BytesMain。", "本地翻译");
            return 1;
        }

        PyBytesMain run = (PyBytesMain)Marshal.GetDelegateForFunctionPointer(fn, typeof(PyBytesMain));
        IntPtr a0 = Marshal.StringToHGlobalAnsi("app.py");
        IntPtr a1 = Marshal.StringToHGlobalAnsi("app.py");
        IntPtr argv = Marshal.AllocHGlobal(IntPtr.Size * 3);
        Marshal.WriteIntPtr(argv, 0, a0);
        Marshal.WriteIntPtr(argv, IntPtr.Size, a1);
        Marshal.WriteIntPtr(argv, IntPtr.Size * 2, IntPtr.Zero);
        return run(2, argv);
    }
}