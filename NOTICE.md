VoxCall 第三方组件说明
=======================

VoxLock（https://github.com/arbazsiddiqui/voxlock）
  许可证：Apache License 2.0
  用途：目标说话人隔离模型（运行时通过 pip 安装，未随本项目分发源码）。
  本项目仅通过其公开 API 调用：VoxLock(...).live() / .stream(sample)。

VB-Audio Virtual Cable（https://vb-audio.com/Cable/ · https://www.vb-cable.com）
  许可证：VB-Audio 自定义授权（donationware / 捐赠软件），作者 Vincent Burel
  用途：Windows 虚拟音频设备（系统级驱动），本程序依赖它把声音送进通话软件。
  分发方式：分发包内**内置官方原版安装包** `assets/vbcable/VBCABLE_Driver_Pack45.zip`
            （未修改、未重新打包，取自 https://download.vb-audio.com/Download_CABLE/）。
            仅在用户机器上未检测到 VB-CABLE 时解压并以管理员身份运行官方安装程序；
            若内置包被移除，则退回从官方地址下载。
  官方条款：https://vb-audio.com/Services/licensing.htm
            「VB-CABLE Distribution with other product」允许随应用分发/内嵌 VB-CABLE 包
            （免费或商业），前提是 donationware 模式仍然适用：终端用户能识别这是
            VB-Audio 的产品，并且仍有机会捐赠/购买许可；需说明来源 www.vb-cable.com
            且 VB-CABLE 是 donationware。VB-CABLE A+B / C+D 不得随产品分发。
            面向公司/机构等"用户无法自行付费"的场景，需按数量在官方商店购买许可。
  本项目对应做法：安装确认框、关于对话框、README 均标注了来源与 donationware 性质，
            并在「关于」里提供「捐赠 VB-CABLE」按钮直达官方商店。
  捐赠/许可：https://shop.vb-audio.com/en/win-apps/11-vb-cable.html

ONNX Runtime（https://github.com/microsoft/onnxruntime）
  许可证：MIT
  用途：VoxLock 的推理后端。

Qt for Python / PySide6（https://doc.qt.io/qtforpython/）
  许可证：LGPL v3（动态链接使用）
  用途：桌面界面。

python-soxr（https://github.com/dofuuz/python-soxr）
  许可证：LGPL v2.1+
  用途：可选的流式重采样后端。

sounddevice / PortAudio
  许可证：MIT（sounddevice） / MIT 类（PortAudio）
  用途：WASAPI 音频 I/O。
