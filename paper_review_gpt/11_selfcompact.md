# SelfCompact：让 agent 判断何时压缩上下文

论文：[Self-Compacting Language Model Agents](https://arxiv.org/pdf/2606.23525v2)，Tianjian Li 等；机构为 Johns Hopkins University 与 Apple。阅读版本：arXiv:2606.23525v2（2026-07-10）。发表状态：NeurIPS 2026 已录用，[Apple 研究页面](https://machinelearning.apple.com/research/self-compacting-language-agents)标注该会议，[会议官网](https://neurips.cc/Downloads/2026)也列入本文；具体轨道未核实。核对日期：2026-10-22。本文依据论文及[作者代码](https://github.com/tianjianl/selfcompact)，未复现实验。

**核心是给 agent 的 harness 加一道检查：让模型判断“现在适不适合把历史压缩成摘要”，再由 harness 真正替换后续输入的上下文。模型参数不更新。**

**1. 场景：一个 agent 做着做着，历史越来越长**

实际实验有两类：

- **数学解题**：推导、总结已有尝试、继续求解或检查，采用 ReasoningCache 的 scaffold。
- **搜索问答**：搜索、读取网页或文档、继续搜索，采用 AggAgent 的 ReAct 流程。

以下主要用搜索场景解释。模型每轮看到的上下文包含原问题、此前的分析、工具调用及搜索结果。SelfCompact 管理的就是这段不断增长的历史。（§4、附录A–B）

**2. 想解决的问题：压缩时机不对，会把有用信息弄丢**

一个**教学例子**：Agent 已排除候选 A，正在比较 B、C。此时达到 token 阈值，系统强制摘要，却漏掉了“为什么排除 A”。下一轮 agent 又开始调查 A。

一直保留完整历史，也可能让重复搜索、旧猜测持续干扰后续判断。因此需要决定：哪些阶段适合收拢已有结果，哪些阶段仍需要保留完整过程。作者把这种对压缩时机的判断作为主要优化对象。（§1–3）

**3. 方法：检查 → 摘要 → 替换历史**

执行任务、判断能否压缩、生成摘要，**使用同一个模型，分成不同调用**；这与实验结束后给最终答案评分的评测器是不同角色。

**第一步：harness 在检查点追加 rubric prompt。** Rubric 就是一张具体检查表。搜索版本要求模型回答：

| 检查项 | 用人话说 |
|---|---|
| C1：当前步骤已完成 | 工具结果已返回，或者一个子分析已经结束 |
| C2：可以安全概括 | 后续所需信息能归纳成 3–5 条有出处的事实 |
| C3：确实有新进展 | 比上次摘要多了事实，或者明确了正在追查的子问题 |
| N1：是否陷入重复搜索 | 最近四次搜索是否至少三次没有获得新 URL 或事实；不足四次时回答否 |

模型要从历史中引用证据支持判断。如果大量排除过程、失败查询无法安全概括，C2 应回答“否”。检查提示和回答在临时消息列表中生成，不作为新的任务历史保留下去。（附录B；[prompts.py](https://github.com/tianjianl/selfcompact/blob/main/prompts.py)、[agent.py](https://github.com/tianjianl/selfcompact/blob/main/agent.py)）

**第二步：代码根据判断执行规则。**

`C1=是 且 C2=是 且 C3=是 且 N1=否 → 压缩`

不满足就继续使用原历史。满足后，再调用同一个模型，要求提取对原问题有用、且历史中明确支持的信息，生成摘要。模型负责判断语义条件，Python 代码负责执行条件组合。

**第三步：harness 重建下一轮输入。**

压缩后的消息列表是：

```text
system prompt
原始问题
模型刚生成的摘要
“继续研究并回答问题”
```

此前完整的搜索与分析历史从后续输入中移除。执行日志可以另行保存，但不会自动重新送入模型。这是真正的上下文替换，不能仅靠在 system prompt 中写“请简洁一些”实现。（§3、Algorithm 1；代码 `run_selfcheck`、`summarize`）

检查本身仍受轮数、token 数等条件控制；通过这些门槛后才询问模型。v2 附录B还保留了超过上下文阈值时强制压缩的兜底，因此并非完全取消阈值。

**数学使用另一套 rubric。** 已有明确答案，或者虽然卡住但能给出具体下一步时，允许压缩；摘要要保留答案或下一步。因此不能把搜索版本的“卡住时不压缩”推广到全部实验。（附录A）

每个实际检查点增加一次模型判断，决定压缩时再增加一次摘要生成。实现将提示追加在原历史后，以利用可用的前缀缓存；额外调用仍有成本，节省主要来自后续输入变短。复现时还应注意：v2 附录B的摘要次数上限和强制阈值，与当前公开代码的默认配置不完全一致。

**4. 训练数据：没有参数训练**

没有 SFT、RL，也没有训练专门的压缩决策模型。新增的是人工写好的检查规则、摘要提示，以及 harness 的控制逻辑。

模型在当前任务历史上临时作判断，不需要读取 hidden states，也没有用于学习这些判断的标注训练集。下文 benchmark 的题目是评测数据，不能计作本方法的训练数据。（§3、§6）

**5. 实验：提升有效，但要看清比较条件**

| 场景 | 模型 | Benchmark 与评测方式 |
|---|---|---|
| 搜索 | GLM-4.7-Flash、MiniMax-M2.5、MiMo-V2-Flash | BrowseComp、BrowseComp-Plus、DeepSearchQA；每集抽取150道题 |
| 数学 | Qwen3-4B-Instruct-2507、Qwen3-30B-A3B-Instruct-2507、Qwen3.5-4B、Qwen3.5-9B | IMO-Answerbench、HMMT Nov 2025、HMMT Feb 2026；每题采样16条回答，统计平均表现 |

BrowseComp 和 DeepSearchQA 使用网页搜索与访问工具；BrowseComp-Plus 在固定语料上检索。数学按标准答案用 `math_verify` 评分；16条回答不是16次前向传播，也不是投票选出一个答案。Qwen3.5 的 Table 1 与附录A均标为 thinking disabled；正文有一处相反措辞，此处采用表格与附录的设置。（§4、附录A–B）

以 **MiniMax-M2.5 在 BrowseComp-Plus** 为例：

| 方法 | 正确率 | 每题 token 费用估算 |
|---|---:|---:|
| 不压缩 | 62.0% | $0.19 |
| 固定阈值摘要 | 65.9% | $0.04 |
| SelfCompact | **71.2%** | $0.07 |

相对不压缩，更准且更便宜；相对固定摘要，更准但更贵。费用是 Table 4 的 token 计费近似，输入统一按缓存价格计算，包含检查和摘要调用；附录D另给区分首次输入与缓存读取的估算，不能把表中金额当成完整服务账单。（Table 4、附录C–D）

Rubric 也有贡献：GLM 三个搜索集的平均正确率，从“模型自行决定、不给具体规则”的 **41.0%** 提高到完整方法的 **46.4%**。（Table 5）

数学侧必须区分预算。Qwen3-4B 在 IMO-Answerbench 上：

| 方法 | 正确率 | 表中平均生成 token 预算 |
|---|---:|---:|
| 不压缩 | 38.9% | 16k |
| 固定摘要 | 41.4% | 44k |
| SelfCompact | 45.5% | 48k |

和不压缩相比的增益，不能全部归因于压缩时机；固定摘要是更接近预算的比较，但也不是严格完全相同的 token 数。（Table 1）

**6. 一条样本走全过程**

论文图1给出一个蘑菇检索案例。以下是**简化转述；摘要措辞为教学演示，不是实际日志逐字摘录**。

问题要求结合蘑菇分类、神话命名和电影线索，找出一种蘑菇。Agent 的轨迹逐步获得 Agaricus、Bon 1983、Clash of the Titans（1981）、Ray Harryhausen 等线索。每个子问题查清且检查通过后，可以将已有结果写入摘要。

一次摘要可理解为：

> 已确认相关属为 Agaricus；电影线索对应 Clash of the Titans（1981）；特效师为 Ray Harryhausen。保留这些事实及来源，继续核对名称。

对应数据流如下：

| 阶段 | 输入与输出 |
|---|---|
| 正常执行 | 原问题和历史送入模型；模型调用搜索或读取工具 |
| 检查 | 当前历史加 rubric；模型给出 C1、C2、C3、N1 及依据 |
| 决策 | 代码检查四项条件；不通过则保留原历史继续 |
| 摘要 | 条件通过后，历史加摘要指令送入同一模型 |
| 重建输入 | harness 保留 system、原问题、摘要及继续指令 |
| 继续执行 | 模型基于较短上下文继续调查并回答 |

作者展示的 SelfCompact 轨迹最终回答 **Medusa mushroom**；固定间隔压缩的轨迹丢失线索，回答成 **Morel mushroom**；不压缩版本耗尽预算而没有给出答案。这是作者的定性展示，不意味着每次压缩都能保留全部关键证据。（Figure 1）

**7. Takeaway**

- **方法归类：harness engineering，具体是上下文管理。** 新增的关键机制是用明确的检查条件，控制摘要何时真正替换历史；模型权重不更新。
- **可借鉴的执行方式是“模型判断条件，程序执行动作”。** 实验支持这种 rubric 能改善被测模型的压缩决策，但收益需要连同生成预算和调用成本一起看。
- **检查对象是当前历史能否安全压缩。** 它没有提供答案正确性的保证，也没有输出经过校准的 confidence。把它用于其他任务时，仍需分别评估答案正确率、信息丢失与成本；论文未验证 coding agent 或规则推理任务上的收益。

来源定位：[论文 HTML v2](https://arxiv.org/html/2606.23525v2) §3–4、Table 1/4/5、附录A–D；[PDF 图1](https://arxiv.org/pdf/2606.23525v2#page=2)；[作者 README](https://github.com/tianjianl/selfcompact)、[提示模板](https://github.com/tianjianl/selfcompact/blob/main/prompts.py)、[运行逻辑](https://github.com/tianjianl/selfcompact/blob/main/agent.py)。代码核对于2026-10-22，当前 main 可能继续变化。
