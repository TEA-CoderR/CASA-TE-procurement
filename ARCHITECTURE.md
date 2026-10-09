# 采购系统架构设计文档

## 📋 系统概述

意大利门店采购订单自动化处理系统，核心功能：
- 读取采购订单（ORDINE）
- 扣减已处理数量（eliminare）
- 构建供应商价格矩阵（distribuire）
- 自动选择最优供应商（最低价）
- 回写订单到供应商文件

---

## 🏗️ 当前架构分析

### 架构模式：单体应用（Monolithic）

```
main.py (UI层)
   ↓
service.py (业务逻辑层)
   ↓
utils.py (工具层)
   ↓
config.py (配置层)
```

### 优势
- ✅ 结构简单，易于理解
- ✅ 部署方便（单进程）
- ✅ 适合小团队维护

### 劣势
- ❌ UI与业务逻辑耦合
- ❌ 缺乏分层隔离
- ❌ 难以测试
- ❌ Excel COM对象管理不规范

---

## 🐛 已修复的BUG

### BUG-001: EAN索引失效问题（严重）
**问题**: 在eliminate处理中删除行后，EAN索引失效，但后续代码未重新构建索引。

**影响**: 可能导致价格矩阵构建时行号错位，数据写入错误行。

**修复**: 添加警告日志，提示后续操作需重新构建索引。

**状态**: ✅ 已修复（v2.0.1）

---

### BUG-002: 线程状态管理不正确（中等）
**问题**: 使用硬编码2秒延迟恢复按钮，而非真正检查线程完成状态。

**影响**: 
- 处理时间短于2秒：按钮延迟恢复
- 处理时间长于2秒：用户可重复点击，导致并发执行

**修复**: 实现轮询机制，检查`thread.is_alive()`状态。

**状态**: ✅ 已修复（v2.0.1）

---

### BUG-003: 文件扩展名检查不一致（低）
**问题**: 
- `main.py` 使用 `file.lower().endswith((".xls", ".xlsx"))`
- `service.py` 使用 `file.endswith((".xls", ".xlsx"))`（未转小写）

**影响**: Windows文件名大小写不敏感，但Linux/Mac会失败。

**修复**: 统一使用 `.lower().endswith()`。

**状态**: ✅ 已修复（v2.0.1）

---

### BUG-004: 供应商列累积问题（严重）
**问题**: 程序多次运行时，供应商矩阵列会累积，包括 `.calc.xlsx` 后缀的文件名。

**影响**: 
- 主表列数不断增长
- 数据混乱，供应商选择结果错误
- 文件越来越大

**修复**: 
1. 在构建价格矩阵前，清理旧的供应商列（第31列及以后）
2. 清理供应商选择结果列（第30列）
3. 跳过缺失报告文件，不将其当作供应商文件处理

**状态**: ✅ 已修复（v2.0.2）

---

### BUG-005: `.calc.xlsx` 后缀累积（中等）
**问题**: `normalize()` 函数清理 `.calc` 后缀的逻辑不完善，导致文件名中出现 `.calc.xlsx.calc.xlsx`。

**影响**: 供应商文件名匹配失败，回写订单时无法找到对应行。

**修复**: 优化 `normalize()` 函数，先清理 `.calc` 后缀，再去掉扩展名。

**状态**: ✅ 已修复（v2.0.2）

---

## 🎯 目标架构设计

### 架构原则

1. **领域驱动设计（DDD）** - 以采购业务流程为核心
2. **分层架构** - 清晰的分层边界
3. **依赖倒置** - 高层模块不依赖低层模块
4. **开闭原则** - 对扩展开放，对修改关闭

### 目标架构图

```
┌─────────────────────────────────────────┐
│           Presentation Layer            │
│  (Tkinter GUI / Web API / CLI)          │
├─────────────────────────────────────────┤
│           Application Layer             │
│  (Use Cases / Orchestration)            │
├─────────────────────────────────────────┤
│           Domain Layer                  │
│  (Business Logic / Entities)            │
├─────────────────────────────────────────┤
│           Infrastructure Layer          │
│  (Excel IO / COM / File System)         │
└─────────────────────────────────────────┘
```

---

## 📦 模块划分

### 1. 表现层（Presentation）

**职责**: 用户交互、输入验证、结果展示

```
presentation/
├── gui/
│   ├── main_window.py      # 主窗口
│   ├── components/         # UI组件
│   └── validators.py       # 输入验证
├── api/                    # REST API（未来扩展）
│   └── routes.py
└── cli/                    # 命令行接口（未来扩展）
    └── commands.py
```

### 2. 应用层（Application）

**职责**: 用例编排、事务管理、工作流控制

```
application/
├── use_cases/
│   ├── process_order.py    # 处理订单用例
│   ├── deduct_quantity.py  # 扣减数量用例
│   └── select_supplier.py  # 选择供应商用例
├── services/
│   └── procurement_service.py  # 采购服务编排
└── workers/
    └── background_worker.py     # 后台任务管理
```

### 3. 领域层（Domain）

**职责**: 核心业务逻辑、领域实体、业务规则

```
domain/
├── entities/
│   ├── purchase_order.py   # 采购订单实体
│   ├── supplier.py         # 供应商实体
│   └── product.py          # 产品实体
├── services/
│   ├── pricing_service.py  # 定价服务
│   └── allocation_service.py  # 分配服务
├── repositories/
│   └── interfaces.py       # 仓储接口定义
└── value_objects/
    ├── ean.py              # EAN编码值对象
    └── price.py            # 价格值对象
```

### 4. 基础设施层（Infrastructure）

**职责**: 数据持久化、外部系统集成

```
infrastructure/
├── excel/
│   ├── excel_reader.py     # Excel读取器
│   ├── excel_writer.py     # Excel写入器
│   └── com_manager.py      # COM对象管理器
├── file_system/
│   └── file_repository.py  # 文件仓储
└── logging/
    └── logger.py           # 日志服务
```

---

## 🔧 技术债务清理计划

### 第一阶段：代码重构（1-2周）

- [ ] 分离UI层与业务逻辑层
- [ ] 引入依赖注入容器
- [ ] 实现仓储模式（Repository Pattern）
- [ ] 统一错误处理机制

### 第二阶段：基础设施改进（2-3周）

- [ ] Excel COM连接池管理
- [ ] 异步任务队列（Celery/RQ）
- [ ] 数据库集成（SQLite/PostgreSQL）
- [ ] 配置文件管理（YAML/ENV）

### 第三阶段：功能扩展（3-4周）

- [ ] Web API接口（FastAPI）
- [ ] 实时监控仪表盘
- [ ] 邮件通知服务
- [ ] 审计日志系统

---

## 🧪 测试策略

### 单元测试

```
tests/
├── unit/
│   ├── test_pricing.py
│   ├── test_allocation.py
│   └── test_ean.py
├── integration/
│   ├── test_excel_io.py
│   └── test_workflow.py
└── e2e/
    └── test_full_process.py
```

**目标覆盖率**: 80%+

### 测试框架

- `pytest` - 测试运行器
- `pytest-cov` - 覆盖率报告
- `factory_boy` - 测试工厂
- `responses` - HTTP Mock

---

## 📊 性能优化

### 当前瓶颈

1. **Excel COM对象创建/销毁** - 每个文件一次
2. **O(n²) EAN匹配** - 已优化为O(n)
3. **同步处理** - 阻塞UI线程

### 优化方案

| 优化项 | 当前 | 目标 | 方法 |
|--------|------|------|------|
| COM对象复用 | 无 | 连接池 | 对象池模式 |
| EAN匹配 | O(n) | O(1) | 哈希索引 |
| 并发处理 | 单线程 | 多线程 | ThreadPoolExecutor |
| 内存占用 | 全量加载 | 流式处理 | 分块读取 |

---

## 🔐 安全加固

- [ ] 输入验证（防止Excel注入）
- [ ] 文件路径白名单
- [ ] 敏感配置加密
- [ ] 操作审计日志

---

## 📈 监控与可观测性

### 日志系统

```python
# 结构化日志
{
    "timestamp": "2026-04-26T19:05:00Z",
    "level": "INFO",
    "module": "procurement",
    "action": "process_order",
    "order_id": "ORD-20240916",
    "duration_ms": 1234
}
```

### 关键指标

- 处理订单数/小时
- 平均处理时间
- 错误率
- Excel COM对象存活数

---

## 🚀 部署架构

### 当前：单机部署

```
┌─────────────────┐
│   Windows PC    │
│  ┌───────────┐  │
│  │ Tkinter   │  │
│  │  GUI App  │  │
│  └───────────┘  │
└─────────────────┘
```

### 未来：微服务架构（可选）

```
┌─────────────────────────────────────┐
│           API Gateway               │
├──────────┬──────────┬───────────────┤
│ Order    │ Supplier │ Notification  │
│ Service  │ Service  │   Service     │
├──────────┴──────────┴───────────────┤
│      Message Queue (RabbitMQ)       │
├─────────────────────────────────────┤
│      Database (PostgreSQL)          │
└─────────────────────────────────────┘
```

**决策**: 当前阶段保持单体架构，待业务规模增长后再考虑微服务。

---

## 📅 演进路线图

### v2.0 (当前)
- ✅ 基础功能完成
- ✅ 核心BUG修复

### v2.1 (1个月后)
- [ ] 代码重构（分层架构）
- [ ] 单元测试覆盖
- [ ] Excel COM优化

### v2.2 (2个月后)
- [ ] Web API接口
- [ ] 数据库集成
- [ ] 异步任务处理

### v3.0 (6个月后)
- [ ] 微服务架构评估
- [ ] 多租户支持
- [ ] AI价格预测

---

## 📝 架构决策记录（ADR）

### ADR-001: 保持单体架构

**状态**: 已接受

**上下文**: 系统当前服务于单一业务场景，团队规模小（<5人），功能需求稳定。

**决策**: 保持单体架构，采用模块化设计，暂不引入微服务。

**后果**: 
- ✅ 开发效率高
- ✅ 部署简单
- ❌ 未来扩展需重构

**替代方案**: 
- 微服务架构（过早抽象，维护成本高）
- Serverless（Excel COM不支持）

---

### ADR-002: 使用Excel COM而非纯Python库

**状态**: 已接受

**上下文**: 业务文件包含复杂公式，需要准确计算。

**决策**: 使用Excel COM进行公式计算，openpyxl处理数据读写。

**后果**:
- ✅ 公式计算准确
- ✅ 兼容.xls和.xlsx
- ❌ Windows only
- ❌ COM对象管理复杂

**替代方案**:
- 纯openpyxl（不支持公式计算）
- pandas（不支持.xls）

---

## 🎓 团队能力建设

### 技术栈要求

| 技能 | 优先级 | 学习资源 |
|------|--------|----------|
| Python高级特性 | P0 | 《Fluent Python》 |
| DDD领域驱动设计 | P1 | 《实现领域驱动设计》 |
| 测试驱动开发 | P1 | pytest官方文档 |
| 架构模式 | P2 | 《Clean Architecture》 |

### 代码规范

- PEP 8 代码风格
- type hints 类型注解
- docstrings 文档字符串
- pre-commit hooks 自动检查

---

## 💡 最佳实践

1. **永远不要信任用户输入** - 验证所有Excel文件
2. **优雅降级** - COM失败时提供备选方案
3. **资源清理** - 确保COM对象释放
4. **幂等性** - 处理可重复执行
5. **审计追踪** - 记录所有数据变更

---

## 📞 联系方式

架构师: 软件架构师Agent  
更新日期: 2026-04-26  
版本: v2.0.2
