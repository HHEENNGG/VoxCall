# 手动下载 VoxLock 模型

## 当前环境需要的模型

本说明对应 `voxcall` Conda 环境中已安装的 **voxlock 1.3.1**，以及项目当前配置
`[model] precision = "int8-full"`。

上游模型仓库：https://huggingface.co/arbazsiddiqui/voxlock

该版本的 `voxlock/core.py` 固定使用提交
`2413b292bc2c337bfc19a9802aaf3e011e68e9c4` 下的 **`onnx/v2/`** 模型，
不要误用仓库里旧版 `onnx/` 下的同名文件。

默认只需要下面两个文件（合计 74,941,980 字节，约 75 MB）：

| 文件 | 用途 | 大小（字节） |
| --- | --- | ---: |
| `enroll.onnx` | 从语音样本提取目标说话人特征 | 21,023,799 |
| `chunk_int8_full.onnx` | 实时分块推理，int8-full 精度 | 53,918,181 |

直接下载链接：

- [enroll.onnx](https://huggingface.co/arbazsiddiqui/voxlock/resolve/2413b292bc2c337bfc19a9802aaf3e011e68e9c4/onnx/v2/enroll.onnx?download=true)
- [chunk_int8_full.onnx](https://huggingface.co/arbazsiddiqui/voxlock/resolve/2413b292bc2c337bfc19a9802aaf3e011e68e9c4/onnx/v2/chunk_int8_full.onnx?download=true)

下载的是完整 ONNX 文件，不是网页，也不是 Git LFS 的文本指针。

## 放置位置

将文件直接放在项目根目录的 `models\onnx` 中：

```text
D:\Github\VoxCall\
├── main.py
├── config.toml
└── models\
    └── onnx\
        ├── enroll.onnx
        └── chunk_int8_full.onnx
```

**不要再嵌套 `v2` 文件夹**。远端路径是 `onnx/v2`，但本项目自动查找的本地路径是
`models/onnx`，且模型文件必须直接位于该目录下。

保持 `config.toml` 中的配置即可：

```toml
[model]
precision = "int8-full"
model_dir = ""
```

`model_dir` 留空时，项目会自动使用上述本地模型目录。
如已有自定义 `model_dir`，它会优先于自动查找；应清空它，或改成实际存放目录。
例如模型存放到别的磁盘时，使用 TOML 正斜杠路径：

```toml
[model]
precision = "int8-full"
model_dir = "E:/AI/voxlock/onnx"
```

显式传入本地 `model_dir` 后，VoxLock 不会调用 Hugging Face 下载函数。
`config.yaml` 虽然会在自动下载流程中一并获取，但本地 ONNX 推理不读取它，
所以手动部署这两个 ONNX 文件时**不需要**下载 `config.yaml`。

项目的 `config.toml` 是 VoxCall 的程序配置，与模型仓库的 `config.yaml` 不是同一文件。

## 可选：PowerShell 下载并校验

在项目根目录运行（不修改系统证书，不关闭 TLS 验证）：

```powershell
New-Item -ItemType Directory -Force .\models\onnx | Out-Null
$base = 'https://huggingface.co/arbazsiddiqui/voxlock/resolve/2413b292bc2c337bfc19a9802aaf3e011e68e9c4/onnx/v2'
Invoke-WebRequest "$base/enroll.onnx?download=true" -OutFile .\models\onnx\enroll.onnx
Invoke-WebRequest "$base/chunk_int8_full.onnx?download=true" -OutFile .\models\onnx\chunk_int8_full.onnx
Get-FileHash .\models\onnx\enroll.onnx, .\models\onnx\chunk_int8_full.onnx -Algorithm SHA256
```

SHA256（来自固定提交的 Hugging Face LFS 元数据）：

```text
enroll.onnx
81f305c9b7a32f13d1401f53bb4c6301826a607c9e807e60912f36ef1b9fae29

chunk_int8_full.onnx
74370a17831ef80bb834d9824f6ba36b3168ebbf472d47c3b23432049c577ad4
```

下载后运行 `.\run-console.bat --doctor`；自动发现模型时，应显示本地模型目录。
随后重新启动程序并点击「开始」。

## 切换精度时需要哪些文件

所有文件都必须来自上述固定提交的 `onnx/v2/`：

| `precision` | 必需文件 |
| --- | --- |
| `int8-full`（当前默认） | `enroll.onnx` + `chunk_int8_full.onnx` |
| `int8` | `enroll.onnx` + `chunk_int8.onnx` |
| `fp16` | `enroll_fp16.onnx` + `chunk_fp16.onnx` |
| `fp32` | `enroll.onnx` + `chunk.onnx` |

无需下载全部精度。若想保留项目的 `int8` 回退路径，可额外下载
[chunk_int8.onnx](https://huggingface.co/arbazsiddiqui/voxlock/resolve/2413b292bc2c337bfc19a9802aaf3e011e68e9c4/onnx/v2/chunk_int8.onnx?download=true)。

## 本机下载失败的原因

本次排查发现 `logs/app.log` 中的下载错误是
`SSL: CERTIFICATE_VERIFY_FAILED`（无法找到本地颁发者证书），不是缺少 Python 依赖。
可先通过浏览器下载并按上述目录部署，绕过 Python 下载流程；若修复自动下载，
应检查本机代理、组织证书和 Conda/Python CA 信任配置，不要关闭证书验证。

## 「上一次未结束」的修复

- 音频工作线程失败后，旧代码只要看到 `_worker` 对象仍存在就禁止重启，
  即使该线程已经退出。现改为在下一次启动前回收已结束线程；仍存活的线程不能被重入。
- Windows 单实例不再仅根据互斥体名称是否存在判定，而是原子检查所有权，
  可接管被异常退出进程遗弃的互斥体，并在启动失败、正常退出时显式释放句柄。
- 唤起旧窗口必须收到准确的 `ok\n` 应答；旧进程仍存活但不响应时给出错误，
  不再静默退出，也不强行开启第二个音频实例。

已打开的旧进程不会自动载入这些修复。请先从托盘退出，再用 `run.bat` 启动新版代码。
如果旧进程卡住，核对任务管理器中命令行指向本项目 `main.py` 的进程后再结束它，
不要批量结束所有 Python 进程。默认关闭窗口只会最小化到托盘，不代表进程已退出。
