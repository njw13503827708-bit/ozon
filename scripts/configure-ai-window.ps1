param([string]$Url='https://ai.opendoor.sbs',[string]$ProjectRoot='',[string]$PythonExecutable='')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$aiRoot=if ($ProjectRoot) { (Resolve-Path -LiteralPath $ProjectRoot).Path } else { (Resolve-Path (Join-Path $PSScriptRoot '..')).Path }
$aiPython=if ($PythonExecutable) { (Resolve-Path -LiteralPath $PythonExecutable).Path } else { Join-Path $aiRoot '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $aiPython -PathType Leaf)) { throw 'Cannot find Python for secure configuration.' }
$aiScript=Join-Path $aiRoot 'scripts/configure_ai.py'
function Invoke-AiLocalPipe([string]$Action,[hashtable]$Payload,[string]$Mode) {
    $aiInfo=New-Object System.Diagnostics.ProcessStartInfo
    $aiInfo.FileName=$aiPython
    $aiInfo.Arguments='"'+$aiScript+'" --url "'+$Url+'" --api-mode '+$Mode+' '+$Action
    $aiInfo.UseShellExecute=$false;$aiInfo.CreateNoWindow=$true
    $aiInfo.RedirectStandardInput=$true;$aiInfo.RedirectStandardOutput=$true;$aiInfo.RedirectStandardError=$true
    $aiInfo.EnvironmentVariables['PYTHONUTF8']='1';$aiInfo.EnvironmentVariables['PYTHONIOENCODING']='utf-8'
    $aiProcess=New-Object System.Diagnostics.Process;$aiProcess.StartInfo=$aiInfo
    [void]$aiProcess.Start()
    $aiProcess.StandardInput.WriteLine(($Payload | ConvertTo-Json -Compress));$aiProcess.StandardInput.Close()
    $aiOutput=$aiProcess.StandardOutput.ReadToEnd();$aiError=$aiProcess.StandardError.ReadToEnd();$aiProcess.WaitForExit()
    if ($aiProcess.ExitCode -ne 0) { throw $aiError.Trim() }
    return ($aiOutput | ConvertFrom-Json)
}
$aiForm=New-Object System.Windows.Forms.Form
$aiForm.Text='ERP 后端模型安全配置';$aiForm.Size=New-Object System.Drawing.Size(650,475);$aiForm.StartPosition='CenterScreen'
$aiLabel=New-Object System.Windows.Forms.Label;$aiLabel.Text="仅保存到本机开发 ERP；不会调用收费生成。`n网关：$Url`n新密钥（密码框正常支持 Ctrl+V；不要使用已暴露旧Key）：";$aiLabel.SetBounds(20,15,555,60)
$aiKey=New-Object System.Windows.Forms.TextBox;$aiKey.UseSystemPasswordChar=$true;$aiKey.SetBounds(20,80,555,28)
$aiModels=New-Object System.Windows.Forms.CheckedListBox;$aiModels.CheckOnClick=$true;$aiModels.SetBounds(20,122,420,210)
$aiRead=New-Object System.Windows.Forms.Button;$aiRead.Text='读取可用模型';$aiRead.SetBounds(455,121,155,29)
$aiMode=New-Object System.Windows.Forms.ComboBox;$aiMode.DropDownStyle='DropDownList';[void]$aiMode.Items.Add('responses');[void]$aiMode.Items.Add('chat_completions');$aiMode.SelectedIndex=0;$aiMode.SetBounds(455,167,155,28)
$aiStatus=New-Object System.Windows.Forms.Label;$aiStatus.SetBounds(20,345,590,52);$aiStatus.Text='读取模型后，勾选可用于文本生成的模型（不要勾选 gpt-image 模型）。将一次保存所有勾选项。'
$aiSave=New-Object System.Windows.Forms.Button;$aiSave.Text='保存已勾选文本模型';$aiSave.SetBounds(420,410,190,32)
$aiRead.Add_Click({
    try { $aiRead.Enabled=$false;$aiStatus.Text='正在只读核对网关模型…';$aiForm.Refresh()
        $aiResult=Invoke-AiLocalPipe '--read-models-stdin' @{api_key=$aiKey.Text} $aiMode.Text
        $aiModels.Items.Clear();foreach($aiId in $aiResult.models) { [void]$aiModels.Items.Add($aiId) }
        $aiStatus.Text='请勾选可用于文本生成的模型；一次保存后可在每个商品生成任务中切换。不要勾选 gpt-image 模型。'
    } catch { $aiStatus.Text=$_.Exception.Message } finally { $aiRead.Enabled=$true }
})
$aiSave.Add_Click({
    try { $aiSave.Enabled=$false
        $aiSelected=@($aiModels.CheckedItems | ForEach-Object { [string]$_ })
        if (-not $aiSelected.Count) { throw '请至少勾选一个文本模型。' }
        $aiResult=Invoke-AiLocalPipe '--configure-stdin' @{api_key=$aiKey.Text;model=$aiSelected[0];models=$aiSelected} $aiMode.Text
        $aiKey.Clear();$aiStatus.Text="配置成功：key_configured=$($aiResult.key_configured) · 已保存$($aiResult.models.Count)个文本模型 · 默认$($aiResult.model)"
    } catch { $aiStatus.Text=$_.Exception.Message } finally { $aiSave.Enabled=$true }
})
$aiForm.Controls.AddRange(@($aiLabel,$aiKey,$aiModels,$aiRead,$aiMode,$aiStatus,$aiSave))
[void]$aiForm.ShowDialog();$aiKey.Clear();$aiForm.Dispose()
