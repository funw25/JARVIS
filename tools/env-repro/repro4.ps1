# Repro v3: CreateProcessWithTokenW with a duplicated token (SeImpersonatePrivilege is enabled).
$ErrorActionPreference = 'Stop'

$code = @'
using System;
using System.Runtime.InteropServices;

public static class R3
{
    [StructLayout(LayoutKind.Sequential)]
    public struct SID_AND_ATTRIBUTES { public IntPtr Sid; public uint Attributes; }
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

    [DllImport("advapi32.dll", SetLastError = true)]
    public static extern bool OpenProcessToken(IntPtr h, uint acc, out IntPtr tok);
    [DllImport("advapi32.dll", SetLastError = true)]
    public static extern bool DuplicateTokenEx(IntPtr tok, uint acc, IntPtr attrs, int impLevel, int tokType, out IntPtr newTok);
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool CreateRestrictedToken(IntPtr exist, uint flags, uint disCount, IntPtr dis,
        uint delCount, IntPtr del, uint resCount, SID_AND_ATTRIBUTES[] resList, out IntPtr newTok);
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool ConvertStringSidToSid(string s, out IntPtr sid);
    [DllImport("advapi32.dll")]
    public static extern int GetLengthSid(IntPtr sid);
    [DllImport("advapi32.dll", SetLastError = true)]
    public static extern bool SetTokenInformation(IntPtr tok, int cls, ref TOKEN_MANDATORY_LABEL info, int len);
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool CreateProcessWithTokenW(IntPtr tok, uint logonFlags, string app, string cmd,
        uint flags, IntPtr env, string cwd, ref STARTUPINFO si, out PROCESS_INFORMATION pi);
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern uint WaitForSingleObject(IntPtr h, uint ms);
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool GetExitCodeProcess(IntPtr h, out uint code);
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll")]
    public static extern IntPtr GetCurrentProcess();

    static string Err(string api) { int e = Marshal.GetLastWin32Error(); return "ERR " + api + " " + e + " (" + new System.ComponentModel.Win32Exception(e).Message + ")"; }

    public static string Run(string exe, string cmdline, string[] restrictingSids, bool lowIntegrity)
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
            if (!CreateRestrictedToken(dup, 0x1 | 0x8, 0, IntPtr.Zero, 0, IntPtr.Zero, (uint)arr.Length, arr, out rt))
                return Err("CreateRestrictedToken");
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

        var si = new STARTUPINFO();
        si.cb = Marshal.SizeOf(typeof(STARTUPINFO));
        PROCESS_INFORMATION pi;
        if (!CreateProcessWithTokenW(useTok, 0, exe, cmdline, 0x08000000, IntPtr.Zero, null, ref si, out pi))
            return Err("CreateProcessWithTokenW");

        WaitForSingleObject(pi.hProcess, 20000);
        uint code;
        GetExitCodeProcess(pi.hProcess, out code);
        CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
        return "exit=0x" + code.ToString("X8") + " (" + code.ToString() + ")";
    }
}
'@


Add-Type -TypeDefinition $code -Language CSharp

$logonSid = ([regex]'S-1-5-5-\d+-\d+').Match((whoami /logonid).ToString()).Value
$cmdExe = "$env:SystemRoot\System32\cmd.exe"
$ps51   = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$ps7    = "C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe"

"logonSID=$logonSid"
"pwsh7 exists: " + (Test-Path $ps7)
"--- pwsh7 version (unconfined) ---"
& $ps7 -NoLogo -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'

$targets = @(
    @{ n = 'cmd.exe';        exe = $cmdExe; a = '/c exit 7' },
    @{ n = 'powershell 5.1'; exe = $ps51;   a = '-NoLogo -NoProfile -NonInteractive -Command "exit 7"' },
    @{ n = 'pwsh 7 (cache)'; exe = $ps7;    a = '-NoLogo -NoProfile -NonInteractive -Command "exit 7"' }
)
$cases = @(
    @{ Name = 'A-low-only';      Sids = @();                     Low = $true  },
    @{ Name = 'B-sandbox-shape'; Sids = @('S-1-1-0', $logonSid); Low = $true  }
)
foreach ($c in $cases) {
    foreach ($t in $targets) {
        $cmdline = '"' + $t.exe + '" ' + $t.a
        $r = [R3]::Run($t.exe, $cmdline, [string[]]$c.Sids, [bool]$c.Low)
        "{0,-16} {1,-18} {2}" -f $c.Name, $t.n, $r
    }
}