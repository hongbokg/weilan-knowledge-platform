# 第三方声明

本仓库根目录 Apache-2.0 只适用于独立编写的集成代码，不重授第三方许可。

| 组件 | 处理方式与许可来源 |
|---|---|
| WeKnora | 固定基础提交的派生补丁保留 server/weknora-overlay/LICENSE 和第三方声明；上游主体 MIT，组件另有许可 |
| Octop | 不打包完整源码或镜像，保留上游 MIT 许可与实际依赖声明 |
| PyMuPDF MuPDF | 原生 PDF 提取依赖，AGPL 或商业许可；不随包重授 Apache |
| MinerU 及视觉模型 | 通过外部服务契约接入；软件、模型权重、衍生文件使用各自许可 |
| AnyDoc | 适配器通过 Go 依赖连接 WeKnora 固定 third_party/anydoc-go，依赖许可保留上游声明 |
| PaddleOCR PaddlePaddle | 独立可选 CPU 补识别依赖；代码与模型按各自发布条款使用 |
| BGE-M3 Ollama llama-cpp-python | 向量及推理依赖分别遵守官方发布许可，不在本包附带模型权重 |

官方许可参考：

- https://github.com/Tencent/WeKnora/blob/main/LICENSE
- https://github.com/TencentCloud/Octop/blob/main/LICENSE
- https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright
- https://github.com/opendatalab/MinerU
- https://github.com/PaddlePaddle/PaddleOCR
- https://huggingface.co/BAAI/bge-m3

公开安装包不包括旧商业知识库平台源码，不因拥有部署授权就推断拥有再分发权。依赖安装后的整体使用和再分发义务应按对应版本许可履行，本文件不是全栈统一 Apache 许可承诺。
