# OCR Service (PaddleOCR)

独立 OCR 服务，基于 PaddleOCR 2.7 提供图片文字识别能力，供 rag-service 通过 HTTP 调用。

## 架构

- 基础镜像：`paddlepaddle:2.6.0-runtime`（CPU）
- 框架：FastAPI + Uvicorn
- 端口：8200
- 模型：PaddleOCR（`use_angle_cls=True`, `lang=ch`），启动时预热避免首请求冷启动

## API

### POST /ocr

form-data：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `file` | UploadFile | 必填 | 图片文件（PNG/JPG 等） |
| `lang` | str | `ch` | PaddleOCR 语言参数 |

Response 200：

```json
{
  "text": "识别出的全文（按行拼接）",
  "layout": [
    {"bbox": [12.0, 34.0, 200.0, 56.0], "text": "单行文本", "score": 0.98}
  ]
}
```

`bbox` 为 `[x_min, y_min, x_max, y_max]` 四浮点矩形包围盒。

错误响应：

- `400 image_decode_failed`：图片解码失败
- `500 ocr_inference_failed`：OCR 推理失败

### GET /health

```json
{"status": "ok"}
```

## 本地构建与运行

```bash
docker build -t ocr-service:local deploy/ocr/
docker run -d --name ocr-service -p 8200:8200 ocr-service:local

# 健康检查
curl http://localhost:8200/health

# 识别图片
curl -F "file=@test.png" -F "lang=ch" http://localhost:8200/ocr
```

## Kubernetes 部署

默认 **3 副本**，保证高可用与吞吐：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ocr-service
  labels:
    app: ocr-service
spec:
  replicas: 3
  selector:
    matchLabels:
      app: ocr-service
  template:
    metadata:
      labels:
        app: ocr-service
    spec:
      containers:
        - name: ocr-service
          image: ocr-service:latest
          ports:
            - containerPort: 8200
          resources:
            requests:
              cpu: "1"
              memory: "2Gi"
            limits:
              cpu: "2"
              memory: "4Gi"
          livenessProbe:
            httpGet:
              path: /health
              port: 8200
            initialDelaySeconds: 30
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /health
              port: 8200
            initialDelaySeconds: 10
            periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: ocr-service
spec:
  selector:
    app: ocr-service
  ports:
    - port: 8200
      targetPort: 8200
```

## 扩缩容

- 默认 3 副本；单副本内存约 2–3 GB（PaddleOCR 模型常驻）
- 手动扩缩容：

```bash
kubectl scale deployment ocr-service --replicas=5
```

- HPA 建议：CPU > 70% 自动扩容，最小 3 副本

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: ocr-service
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: ocr-service
  minReplicas: 3
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

## 依赖说明

- `pdf2image` 已在 requirements.txt 中声明，若需 PDF OCR 需在镜像内额外安装 `poppler-utils`
- 当前 `app.py` 仅处理单张图片，PDF 多页解析由调用方（rag-service）负责切片后逐张调用
