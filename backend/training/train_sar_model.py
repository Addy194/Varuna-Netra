"""Train a tiny reproducible SAR pixel classifier for the offline demo.

This is intentionally dependency-light so the repository can bootstrap without a
large ML framework. It is a DEMONSTRATION model trained on synthetic SAR-like chips.
It must not be represented as field-validated Sentinel-1 performance.
"""
from pathlib import Path
import json, cv2, numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "models" / "sar_spill_pixel_v1.json"


def features(img: np.ndarray) -> np.ndarray:
    x = img.astype(np.float32) / 255.0
    local = cv2.GaussianBlur(x, (0,0), 2.0)
    gx = cv2.Sobel(x, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(x, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx*gx + gy*gy)
    smooth = cv2.GaussianBlur(grad, (0,0), 1.2)
    coh = cv2.GaussianBlur(x*x, (0,0), 2) - local*local
    return np.stack([1.0-x, np.abs(x-local), smooth, grad, np.maximum(coh,0)], axis=-1)


def make_chip(rng: np.random.Generator, n=96):
    base = np.clip(rng.normal(0.56, 0.12, (n,n)), 0.03, 0.98).astype(np.float32)
    yy, xx = np.mgrid[:n,:n]
    mask = np.zeros((n,n), np.float32)
    for _ in range(int(rng.integers(1,4))):
        cx, cy = rng.uniform(20,n-20,2)
        ang = rng.uniform(0, np.pi)
        major = rng.uniform(10, 28); minor = rng.uniform(2.5, 7)
        xr = (xx-cx)*np.cos(ang)+(yy-cy)*np.sin(ang)
        yr = -(xx-cx)*np.sin(ang)+(yy-cy)*np.cos(ang)
        m = np.exp(-0.5*((xr/major)**2+(yr/minor)**2))
        mask = np.maximum(mask, (m > 0.35).astype(np.float32))
    base -= mask*rng.uniform(0.16,0.28)
    # low-frequency ocean texture + speckle
    base += cv2.GaussianBlur(rng.normal(0,0.07,(n,n)).astype(np.float32),(0,0),3)
    base += rng.normal(0,0.035,(n,n)).astype(np.float32)
    base = np.clip(base,0,1)
    return (base*255).astype(np.uint8), mask


def fit(seed=194, chips=180):
    rng=np.random.default_rng(seed); X=[]; y=[]
    for _ in range(chips):
        img,mask=make_chip(rng)
        f=features(img).reshape(-1,5)
        # balance a sample of positive/negative pixels
        pos=np.where(mask.reshape(-1)>0.5)[0]
        neg=np.where(mask.reshape(-1)<0.5)[0]
        rng.shuffle(pos); rng.shuffle(neg)
        k=min(len(pos), 1800); k2=min(len(neg), 1800)
        X.append(np.concatenate([f[pos[:k]],f[neg[:k2]]]))
        y.append(np.concatenate([np.ones(k), np.zeros(k2)]))
    X=np.concatenate(X); y=np.concatenate(y)
    rng=np.random.default_rng(seed+1); idx=rng.permutation(len(y)); split=int(len(y)*0.8)
    tr,te=idx[:split],idx[split:]
    # Standardize then closed-form ridge logistic approximation via iterative Newton steps.
    mu=X[tr].mean(0); sd=X[tr].std(0)+1e-6
    A=(X[tr]-mu)/sd; z=y[tr]
    w=np.zeros(A.shape[1]+1, dtype=np.float64); A1=np.c_[np.ones(len(A)),A]
    for _ in range(30):
        p=1/(1+np.exp(-np.clip(A1@w,-30,30)))
        g=A1.T@(p-z) + 0.25*np.r_[0,w[1:]]
        H=(A1.T*(p*(1-p)))@A1 + np.diag(np.r_[0,np.full(A.shape[1],0.25)]) + np.eye(A1.shape[1])*1e-5
        w -= np.linalg.solve(H,g)
    At=(X[te]-mu)/sd; pt=1/(1+np.exp(-np.clip(np.c_[np.ones(len(At)),At]@w,-30,30)))
    pred=(pt>=0.5).astype(np.int32); yt=y[te].astype(np.int32)
    tp=((pred==1)&(yt==1)).sum(); fp=((pred==1)&(yt==0)).sum(); fn=((pred==0)&(yt==1)).sum()
    precision=float(tp/max(tp+fp,1)); recall=float(tp/max(tp+fn,1)); f1=2*precision*recall/max(precision+recall,1e-9)
    payload={"model_id":"sar_spill_pixel_v1","type":"logistic_pixel_classifier","feature_names":["darkness","local_contrast","smoothness","gradient","coherence"],"bias":float(w[0]),"weights":[float(x) for x in w[1:]],"mean":[float(x) for x in mu],"scale":[float(x) for x in sd],"training":{"dataset":"synthetic_sar_demo_v1","chips":chips,"seed":seed,"validation":"synthetic-demo-only; real Sentinel-1 labelled validation required before operational use","holdout_metrics":{"precision":round(precision,4),"recall":round(recall,4),"f1":round(f1,4)}}}
    OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text(json.dumps(payload,indent=2)+"\n",encoding='utf-8')
    print(json.dumps(payload["training"]["holdout_metrics"], indent=2))

if __name__=='__main__': fit()
