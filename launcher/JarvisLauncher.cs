using System;
using System.Diagnostics;
using System.IO;
using System.Management;
using System.Windows.Forms;

static class JarvisLauncher
{
    const string Title = "J.A.R.V.I.S.";

    [STAThread]
    static int Main(string[] args)
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        string python = Path.Combine(dir, @".venv\Scripts\pythonw.exe");
        string main = Path.Combine(dir, "main.py");

        if (!File.Exists(main))
        {
            MessageBox.Show("Не нашёл main.py рядом с Jarvis.exe.\nПоложите Jarvis.exe в папку JARVIS.", Title,
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        if (!File.Exists(python) && !Install(dir, python))
            return 1;
        if (AlreadyRunning(main))
        {
            MessageBox.Show("JARVIS уже запущен — он в трее и в мини-ядре на рабочем столе.", Title,
                            MessageBoxButtons.OK, MessageBoxIcon.Information);
            return 0;
        }
        var start = new ProcessStartInfo(python, Quote(main) + " " + JoinArgs(args));
        start.WorkingDirectory = dir;
        start.UseShellExecute = false;
        try
        {
            Process.Start(start);
        }
        catch (Exception ex)
        {
            MessageBox.Show("Не удалось запустить JARVIS: " + ex.Message, Title, MessageBoxButtons.OK,
                            MessageBoxIcon.Error);
            return 1;
        }
        return 0;
    }

    static bool Install(string dir, string python)
    {
        string installer = Path.Combine(dir, "install.bat");
        if (!File.Exists(installer))
        {
            MessageBox.Show("JARVIS не установлен, и install.bat не найден.", Title, MessageBoxButtons.OK,
                            MessageBoxIcon.Error);
            return false;
        }
        var answer = MessageBox.Show("JARVIS ещё не установлен на этом компьютере.\n\nУстановить сейчас? " +
                                     "Откроется окно установки, это займёт несколько минут.", Title,
                                     MessageBoxButtons.YesNo, MessageBoxIcon.Question);
        if (answer != DialogResult.Yes)
            return false;
        var start = new ProcessStartInfo("cmd.exe", "/c " + Quote(installer));
        start.WorkingDirectory = dir;
        start.UseShellExecute = false;
        using (var p = Process.Start(start))
            p.WaitForExit();
        if (File.Exists(python))
            return true;
        MessageBox.Show("Установка не завершилась. Посмотрите сообщения в окне установки.", Title,
                        MessageBoxButtons.OK, MessageBoxIcon.Error);
        return false;
    }

    static bool AlreadyRunning(string main)
    {
        try
        {
            string query = "SELECT CommandLine FROM Win32_Process WHERE Name LIKE 'python%'";
            using (var searcher = new ManagementObjectSearcher(query))
            {
                foreach (ManagementObject p in searcher.Get())
                {
                    var line = p["CommandLine"] as string;
                    if (line != null && line.IndexOf(main, StringComparison.OrdinalIgnoreCase) >= 0)
                        return true;
                }
            }
        }
        catch (Exception)
        {
        }
        return false;
    }

    static string Quote(string s)
    {
        return "\"" + s + "\"";
    }

    static string JoinArgs(string[] args)
    {
        var parts = new string[args.Length];
        for (int i = 0; i < args.Length; i++)
            parts[i] = args[i].IndexOf(' ') >= 0 ? Quote(args[i]) : args[i];
        return string.Join(" ", parts);
    }
}
