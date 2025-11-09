import argparse, random, time, math, requests

def poisson_lam(lam):  
    k = 0
    t = 0.0
    L = math.exp(-lam)
    u = random.random()
    while u > L:
        k += 1
        u *= random.random()
    return k

def post_access(api, key, n):
    import requests
    try:
        requests.post(f"{api}/simulate", params={"key": key, "events": n}, timeout=2)
    except Exception:
        pass

def make_keys(n_hot, n_warm, n_cold):
    keys = []
    for i in range(n_hot):
        keys.append((f"hot/{i:04d}.obj", "hot"))
    for i in range(n_warm):
        keys.append((f"warm/{i:04d}.obj", "warm"))
    for i in range(n_cold):
        keys.append((f"cold/{i:04d}.obj", "cold"))
    random.shuffle(keys)
    return keys

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://api:8000")
    ap.add_argument("--minutes", type=int, default=30)
    ap.add_argument("--hot",  type=int, default=50)
    ap.add_argument("--warm", type=int, default=150)
    ap.add_argument("--cold", type=int, default=300)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    random.seed(args.seed)

    keys = make_keys(args.hot, args.warm, args.cold)
    print(f"Generating {len(keys)} keys for {args.minutes} minutes…")

    base = {
        "hot":  0.8,  
        "warm": 0.12,  
        "cold": 0.01,  
    }

    per_key_mul = {k: random.uniform(0.6, 1.6) for k, _t in keys}
    next_burst = {k: time.time() + random.randint(60, 240) for k, _t in keys}

    end = time.time() + args.minutes*60
    while time.time() < end:
        now = time.time()
        hour = (int(now/3600) % 24)
        diurnal = 1.4 if 10 <= hour <= 22 else 0.7

        for key, tier in keys:
            lam = base[tier] * per_key_mul[key] * diurnal
            n = poisson_lam(lam)
            post_access(args.api, key, n)

            if tier != "hot" and now >= next_burst[key] and random.random() < 0.05:
                post_access(args.api, key, random.randint(30, 120))
                next_burst[key] = now + random.randint(120, 360)

        time.sleep(1)

if __name__ == "__main__":
    main()
