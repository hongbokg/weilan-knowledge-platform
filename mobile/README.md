# Android 客户端源码

原生 Java 界面，不使用 WebView。公开默认域名为 `https://kb.example.com`，请在客户端设置中填写自己的 HTTPS 服务地址并登录。模型密钥在服务器配置，手机不持有提供商密钥。

需要 JDK 17、Android SDK platform 35 和 build-tools 35.0.0；设置 `JAVA_HOME`、`ANDROID_SDK_ROOT`。Windows 的 AAPT2 原生工具对中文路径支持有限，请将源码与 SDK 放在英文路径下构建。

```bash
python build.py
# 自己准备签名 keystore，密码通过环境变量 APK_SIGNING_PASSWORD 传入
python build.py --keystore /private/release.jks --alias weilan
python tests/run_contract_tests.py
python tests/run_firewall_tests.py
```

合约测试另需 Python cryptography。测试使用合成 HTTPS/WSS 服务器；首次下载公开 Maven org.json 测试依赖。测试密钥只在忽略的 build 目录生成。签名私钥不交付，未签名 APK 不能直接安装。JVM 测试不证明 Android 真机行为，发布前应验证登录、原件字节、分享、断网恢复和权限。
