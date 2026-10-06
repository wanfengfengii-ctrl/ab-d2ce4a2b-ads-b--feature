# ADS-B Paired Position Decoder

航空监视平台的前置核对服务：在雷达融合之前，对成对的 ADS-B 空中位置报文
（DF17, TC 9–18 / 20–22）做裁决，并按全球 CPR 规则解出较新一帧的位置，
避免位区交界与经度回绕把航迹投到错误半球。

## API

### `GET /health`

健康检查，返回 `{"status": "ok"}`。

### `POST /api/adsb/positions/decode`

接收 **1–200 组**编号唯一的报文对，组内逐项返回裁决，互不影响。

请求：

```json
{
  "includeAltitude": true,
  "pairs": [
    {
      "id": "pair-001",
      "frames": [
        {"time_ms": 1759700005000, "raw": "8D40621D58C382D690C8AC2863A7"},
        {"time_ms": 1759700000000, "raw": "8D40621D58C386435CC412692AD6"}
      ]
    }
  ]
}
```

下面的响应即对应 `includeAltitude: true`；省略该字段或传 `false` 时，
`position` 中不会出现 `altitude` 键，其余字段完全一致。

- `id`：组编号，批内唯一（字符串，数字会被自动转为字符串）。
- `time_ms`：接收时刻，纪元毫秒。
- `raw`：28 位十六进制（112 比特 Mode S 帧）。
- `includeAltitude`：可选，省略或为 `false` 时响应契约不变（成功结果中没有
  `altitude` 字段）。设为 `true` 时，成功结果额外返回**较新帧**的数值高度及其
  垂直基准，保证水平位置、高度来自同一条较新报文，不会混用气压与几何高度：

  | 类型码 | `reference` | `unit` | `value` |
  | --- | --- | --- | --- |
  | 9–18 | `barometric` | `ft` | 整数；兼容 25 英尺（Q 位）与 Gillham 百英尺编码 |
  | 20–22 | `gnss` | `m` | 无符号整数（12 位原始字段，LSB = 1 m） |

  接收时刻相等时，位置与高度都取自偶帧。若较新帧的高度字段全零、保留或无法
  解释，该组返回 `ALTITUDE_UNAVAILABLE`；高度仅取较新帧，较旧帧的坏高度不影响
  裁决。

合格组的裁决条件（任一不满足即返回稳定错误码并标明编号）：

1. 两帧均为 CRC 校验正确的 DF17 空中位置报文；
2. 两帧 ICAO 地址一致；
3. 奇偶标志相反（一偶一奇）；
4. 接收间隔不超过 10 秒；
5. 两帧落在同一纬度带（NL 一致）。

响应（HTTP 200；每组恰好 `position` / `error` 之一）：

```json
{
  "results": [
    {
      "id": "pair-001",
      "status": "ok",
      "position": {
        "lat": 52.257202,
        "lon": 3.919373,
        "time_ms": 1759700005000,
        "icao": "40621D",
        "frame": "even",
        "altitude": {
          "value": 38000,
          "unit": "ft",
          "reference": "barometric"
        }
      },
      "error": null
    },
    {
      "id": "pair-002",
      "status": "error",
      "position": null,
      "error": {"code": "CRC_MISMATCH", "message": "frame 0: CRC parity check failed"}
    }
  ]
}
```

`altitude` 仅在请求带 `includeAltitude: true` 且较新帧高度可解时出现；省略
`includeAltitude` 时 `position` 中不含该字段。

位置取**较新一帧**，纬度/经度为十进制度、六位小数；经度归一到
`[-180, 180)`，跨日期变更线（如 179.98° → -179.98°）仍落在正确一侧。

稳定错误码：

| 代码 | 含义 |
| --- | --- |
| `INVALID_MESSAGE` | 原文不是 28 位十六进制 |
| `CRC_MISMATCH` | CRC 校验失败 |
| `NOT_DF17` | 下行格式不是 DF17 |
| `NOT_AIRBORNE_POSITION` | 类型码不是空中位置（9–18、20–22） |
| `ICAO_MISMATCH` | 两帧 ICAO 地址不一致 |
| `SAME_CPR_FLAG` | 两帧奇偶标志相同 |
| `TIME_GAP_EXCEEDED` | 两帧相隔超过 10 秒 |
| `LATITUDE_ZONE_MISMATCH` | 两帧纬度带（NL）不一致 |
| `ALTITUDE_UNAVAILABLE` | 启用 `includeAltitude` 后，较新帧的高度全零、保留或无法解释 |
| `INTERNAL_ERROR` | 未预期的单组失败（不会波及其他组） |

请求级校验失败（组数超出 1–200、编号重复、帧数不为 2 等）返回 HTTP 422。

## 运行

```bash
# 构建并启动 API（宿主机端口默认 8000，可用 API_PORT 覆盖）
docker compose up --build api
API_PORT=9000 docker compose up api
```

## 一次性验证

`verify` 服务完成：镜像构建（与 api 共用同一镜像）、代码测试
（pytest）、以及接口冒烟（健康检查、有效报文对、坏 CRC、混合批次与
`includeAltitude` 高度通道），随后自行退出，
并以退出码报告结果（0 = 全部通过）：

```bash
docker compose up --build --exit-code-from verify verify
echo $?   # 0 表示测试与冒烟全部通过
```

## 本地开发

```bash
pip install -r requirements.txt
python -m pytest tests -q
uvicorn app.main:app --port 8000
python scripts/smoke.py http://localhost:8000
```

## 结构

```
app/
  adsb.py     # Mode S CRC(多项式 0x1FFF409)、DF17 帧解析、测试报文构造
  cpr.py      # 全球 CPR 解码（NL 纬度带、经度归一）与编码（测试用）
  altitude.py # 高度解码：25 英尺/Gillham 百英尺气压高度与 GNSS 米制高度
  decoder.py  # 成对裁决：校验顺序、10 秒窗、较新帧选取、高度与位置同源
  schemas.py  # 请求/响应模型（1–200 组、编号唯一、可选 includeAltitude）
  main.py     # FastAPI 入口：/health 与解码端点
tests/        # 单元与接口测试（含日期变更线、纬度分区边界、高度编码用例）
scripts/smoke.py  # 冒烟：健康检查 + 有效报文 + 坏 CRC + 混合批次 + 高度通道
```
