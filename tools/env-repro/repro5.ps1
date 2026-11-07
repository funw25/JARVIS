# Repro v5: same token matrix, but with valid redirected stdio (files), so a failure
# cannot be blamed on missing std handles.
$ErrorActionPreference = 'Stop'

$code = @'
using System;
using System.Runtime.InteropServices;

public static class R5
{
    [StructLayout(LayoutKind.Sequential)]
    public struct SID_AND_ATTRIBUTES { public IntPtr Sid; public uint Attributes; }
    [StructLayout(LayoutKind.Sequential)]
    public struct SECURITY_ATTRIBUTES { public int nLength; public IntPtr lpSecurityDescriptor; public int bInheritHandle; }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct STARTUPINFO
    {
        public int cb; public string lpReserved; public string lpDesktop; public string lpTitle;
        public int dwX; public int dwY; public int dwXSize; public int dwYSize;
        public int dwXCountChars; public int dwYCountChars; public int dwFillAttribute; public int dwFlags;
        public short wShowWindow; public short cbReserved2; public IntPtr lpReserved2;
        public IntPtr hStdInput; public IntPtr hStdOutput; public IntPtr hStdError;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct PROCESS_INFORMATION { public IntPtr hProcess; public IntPtr hThread; public int dwProcessId; public int dwThreadId; }
    [StructLayout(LayoutKind.Sequential)]
    public struct TOKEN_MANDATORY_LABEL { public SID_AND_ATTRIBUTES Label; }

    [DllImport("advapi32.dll", SetLastError = true)] public static extern bool OpenProcessToken(IntPtr h, uint acc, out IntPtr tok);
    [DllImport("advapi32.dll", SetLastError = true)] public static extern bool DuplicateTokenEx(IntPtr tok, uint acc, IntPtr attrs, int impLevel, int tokType, out IntPtr newTok);
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool CreateRestrictedToken(IntPtr exist, uint flags, uint disCount, IntPtr dis, uint delCount, IntPtr del, uint resCount, SID_AND_ATTRIBUTES[] resList, out IntPtr newTok);
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)] public static extern bool ConvertStringSidToSid(string s, out IntPtr sid);
    [DllImport("advapi32.dll")] public static extern int GetLengthSid(IntPtr sid);
    [DllImport("advapi32.dll", SetLastError = true)] public static extern bool SetTokenInformation(IntPtr tok, int cls, ref TOKEN_MANDATORY_LABEL info, int len);
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool CreateProcessWithTokenW(IntPtr tok, uint logonFlags, string app, string cmd, uint flags, IntPtr env, string cwd, ref STARTUPINFO si, out PROCESS_INFORMATION pi);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern IntPtr CreateFile(string name, uint access, uint share, ref SECURITY_ATTRIBUTES sa, uint disposition, uint flags, IntPtr templ);
    [DllImport("kernel32.dll", SetLastError = true)] public static extern uint WaitForSingleObject(IntPtr h, uint ms);
    [DllImport("kernel32.dll", SetLastError = true)] public static extern bool GetExitCodeProcess(IntPtr h, out uint code);
    [DllImport("kernel32.dll", SetLastError = true)] public static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll")] public static extern IntPtr GetCurrentProcess();

    static string Err(string api) { int e = Marshal.GetLastWin32Error(); return "ERR " + api + " " + e + " (" + new System.ComponentModel.Win32Exception(e).Message + ")"; }

    public static string Run(string exe, string cmdline, string[] restrictingSids, bool lowIntegrity, string outFile)
    {
        IntPtr self;
        if (!OpenProcessToken(GetCurrentProcess(), 0xF01FF, out self)) return Err("OpenProcessToken");
        IntPtr dup;
        if (!DuplicateTokenEx(self, 0xF01FF, IntPtr.Zero, 2, 1, out dup)) return Err("DuplicateTokenEx");
        IntPtr useTok = dup;

        if (restrictingSids != null && restrictingSids.Length > 0)
        {
            var arr = new SID_AND_ATTRIBUTES[restrictingSids.Length];
            for (int i = 0; i < restrictingSids.Length; i++)
            {
                IntPtr sid;
                if (!ConvertStringSidToSid(restrictingSids[i], out sid)) return Err("ConvertStringSidToSid " + restrictingSids[i]);
                arr[i].Sid = sid; arr[i].Attributes = 0;
            }
            IntPtr rt;
            if (!CreateRestrictedToken(dup, 0x1 | 0x8, 0, IntPtr.Zero, 0, IntPtr.Zero, (uint)arr.Length, arr, out rt)) return Err("CreateRestrictedToken");
            useTok = rt;
        }
        if (lowIntegrity)
        {
            IntPtr lsid;
            if (!ConvertStringSidToSid("S-1-16-4096", out lsid)) return Err("low sid");
            var ml = new TOKEN_MANDATORY_LABEL();
            ml.Label.Sid = lsid; ml.Label.Attributes = 0x20;
            int len = Marshal.SizeOf(typeof(TOKEN_MANDATORY_LABEL)) + GetLengthSid(lsid) - IntPtr.Size;
            if (!SetTokenInformation(useTok, 25, ref ml, len)) return Err("SetTokenInformation");
        }

        var sa = new SECURITY_ATTRIBUTES();
        sa.nLength = Marshal.SizeOf(typeof(SECURITY_ATTRIBUTES));
        sa.bInheritHandle = 1;
        IntPtr hOut = CreateFile(outFile, 0x40000000 /*GENERIC_WRITE*/, 3 /*FILE_SHARE_READ|WRITE*/, ref sa, 2 /*CREATE_ALWAYS*/, 0x80 /*NORMAL*/, IntPtr.Zero);
        if (hOut == new IntPtr(-1)) return Err("CreateFile");

        var si = new STARTUPINFO();
        si.cb = Marshal.SizeOf(typeof(STARTUPINFO));
        si.dwFlags = 0x100; // STARTF_USESTDHANDLES
        si.hStdInput = hOut; si.hStdOutput = hOut; si.hStdError = hOut;

        PROCESS_INFORMATION pi;
        if (!CreateProcessWithTokenW(useTok, 0, exe, cmdline, 0x08000000, IntPtr.Zero, null, ref si, out pi))
        {
            int e = Marshal.GetLastWin32Error();
            CloseHandle(hOut);
            return "ERR CreateProcessWithTokenW " + e + " (" + new System.ComponentModel.Win32Exception(e).Message + ")";
        }
        WaitForSingleObject(pi.hProcess, 20000);
        uint code;
        GetExitCodeProcess(pi.hProcess, out code);
        CloseHandle(pi.hThread); CloseHandle(pi.hProcess); CloseHandle(hOut);
        return "exit=0x" + code.ToString("X8") + " (" + code.ToString() + ")";
    }
}
'@

Add-Type -TypeDefinition $code -Language CSharp

$logonSid = ([regex]'S-1-5-5-\d+-\d+').Match((whoami /logonid).ToString()).Value
$dir = 'C:\Users\Admin\AppData\Local\Temp\dsh-repro\io'
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$targets = @(
    @{ n = 'cmd.exe';        exe = "$env:SystemRoot\System32\cmd.exe"; a = '/c echo MARKER-CMD && exit 7' },
    @{ n = 'powershell 5.1'; exe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"; a = '-NoLogo -NoProfile -NonInteractive -Command "Write-Output MARKER-PS; exit 7"' }
)
$cases = @(
    @{ Name = 'A-no-restrict';   Sids = @();                     Low = $false },
    @{ Name = 'B-low-only';      Sids = @();                     Low = $true  },
    @{ Name = 'C-everyone-only'; Sids = @('S-1-1-0');            Low = $true  },
    @{ Name = 'D-full-shape';    Sids = @('S-1-1-0', $logonSid); Low = $true  }
)

foreach ($c in $cases) {
    foreach ($t in $targets) {
        $tag = ($c.Name + '_' + ($t.n -replace '[^a-zA-Z0-9]', ''))
        $file = Join-Path $dir ($tag + '.txt')
        $r = [R5]::Run($t.exe, ('"' + $t.exe + '" ' + $t.a), [string[]]$c.Sids, [bool]$c.Low, $file)
        $content = ''
        if (Test-Path $file) { $content = [string](Get-Content -Raw -LiteralPath $file -ErrorAction SilentlyContinue) }
        $content = ([regex]::Replace($content, '\s+', ' ')).Trim()
        if ($content.Length -gt 38) { $content = $content.Substring(0, 38) }
        "{0,-16} {1,-16} {2,-28} out='{3}'" -f $c.Name, $t.n, $r, $content
    }
}