# Manufacturing Plan – Data Architecture

## 1. Kiến trúc tổng thể

```mermaid
flowchart LR
    U([Client / User]) -->|"1. Request: excel path, version, ..."| CP

    subgraph ORCH["Orchestration Layer"]
        CP["Copilot<br/>(Orchestrator Agent)"]
        SA["Sub-agent<br/>Excel Reader"]
        QB["Query Builder"]
        RE["Rule Engine<br/>(customer-defined rules)"]
        PW["Plan Writer<br/>(Excel generator)"]
    end

    subgraph SP["SharePoint"]
        IN[("Input Excel<br/>(config / template)")]
        OUT[("Output Excel<br/>Manufacturing Plan")]
    end

    subgraph FAB["Microsoft Fabric"]
        LH[("Lakehouse / Warehouse<br/>(orders, inventory, BOM,<br/>capacity, ...)")]
    end

    RULES[("Rule Store<br/>(rule config / versioned)")]

    CP -->|"2. file path + version"| SA
    SA -->|"3. read"| IN
    SA -->|"4. required fields"| QB
    QB -->|"5. SQL / DAX / KQL"| LH
    LH -->|"6. result set"| RE
    RULES -.->|"business rules"| RE
    RE -->|"7. plan dataset"| PW
    PW -->|"8. write xlsx"| OUT
    PW -->|"9. file URL"| CP
    CP -->|"10. hyperlink"| U
```

## 2. Sequence

```mermaid
sequenceDiagram
    autonumber
    actor C as Client
    participant CP as Copilot (Orchestrator)
    participant SA as Sub-agent (Excel Reader)
    participant SP as SharePoint
    participant FB as Fabric
    participant RE as Rule Engine
    participant PW as Plan Writer

    C->>CP: Request (excel path, version, ...)
    CP->>SA: Parse request → file ref
    SA->>SP: Get input Excel (path, version)
    SP-->>SA: Excel content
    SA-->>CP: Required fields (+ filters, period, ...)
    CP->>FB: Query built from required fields
    FB-->>CP: Result set
    CP->>RE: Data + customer rules
    RE-->>CP: Manufacturing plan (structured)
    CP->>PW: Plan → Excel
    PW->>SP: Save Manufacturing_Plan_<ts>.xlsx
    SP-->>PW: File URL
    PW-->>CP: URL
    CP-->>C: Hyperlink to Excel
```

## 3. Thành phần & dữ liệu

| # | Thành phần | Input | Output | Ghi chú |
|---|-----------|-------|--------|---------|
| 1 | Copilot (orchestrator) | Query từ client | Điều phối các bước, trả hyperlink | Giữ context/run-id |
| 2 | Sub-agent Excel Reader | path, version | Required fields (JSON) | Dùng Graph API / SharePoint connector |
| 3 | Query Builder | Required fields | Câu query Fabric (parameterized) | Chỉ sinh query từ whitelist field/bảng |
| 4 | Fabric | Query | Result set | Lakehouse/Warehouse SQL endpoint |
| 5 | Rule Engine | Result set + rules | Manufacturing plan | Rules versioned, tách khỏi code |
| 6 | Plan Writer | Plan | Excel trên SharePoint + URL | Tên file có timestamp/version |

## 4. Điểm cần chốt với khách

- Cấu trúc Excel đầu vào: field nào là "required field", version xác định thế nào.
- Rule-logic của khách: dạng nào (Excel/JSON/code), ai sở hữu, versioning.
- Auth: Entra ID / managed identity cho Fabric & SharePoint; phân quyền theo user.
- Output: ghi đè hay tạo file mới mỗi lần; quyền truy cập link.
- Lỗi/audit: log run-id, input version, query, rule version để truy vết.
