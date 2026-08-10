from datetime import datetime, timezone


class PaperEngine:

    def __init__(self):
        # Make sure these exist in your project
        # If they don't, you'll need to adjust
        self.client = self.get_client()

    # ---------- REQUIRED HELPERS ----------
    def get_client(self):
        # You MUST already have this somewhere in your project
        # Replace this if your client is initialized differently
        try:
            return self.client
        except:
            raise Exception("Client not initialized. Fix get_client()")

    def con(self):
        # Your DB connection method must exist
        # If not, this will fail — fix accordingly
        raise NotImplementedError("Define DB connection method")

    # ---------- CONFIG ----------
    def config(self):
        try:
            c = self.con()
            q = c.cursor()
            q.execute("SELECT k, v FROM settings")
            x = dict(q.fetchall())
        except Exception as e:
            print("Config error:", e)
            x = {}
        finally:
            try:
                q.close()
                c.close()
            except:
                pass
        return x

    def save_config(self, *a):
        try:
            vals = dict(zip(
                ["capital", "risk_pct", "max_pos", "min_score", "max_risk", "slippage_bps"],
                a
            ))

            c = self.con()
            q = c.cursor()

            for k, v in vals.items():
                q.execute(
                    "INSERT INTO settings VALUES(%s,%s) "
                    "ON CONFLICT(k) DO UPDATE SET v=EXCLUDED.v",
                    (k, str(v))
                )

            c.commit()

        except Exception as e:
            print("Save config error:", e)

        finally:
            try:
                q.close()
                c.close()
            except:
                pass

    # ---------- CORE ENGINE ----------
    def run_once(self):
        import traceback

        try:
            print("=== ENGINE START ===")

            # 🔴 REAL FIX: use actual scanner
            signals = self.client.scan()

            if signals is None:
                print("WARNING: scan returned None")
                signals = []

            print(f"Signals found: {len(signals)}")

            if len(signals) > 0:
                print("Sample signals:", signals[:3])
            else:
                print("No signals found")

            # ---------- SAVE ----------
            if len(signals) > 0:
                try:
                    self.save(signals)
                    print("Saved to DB")
                except Exception as e:
                    print("Save error:", e)
            else:
                print("Nothing to save")

            print("=== ENGINE END ===")

        except Exception as e:
            print("FATAL ERROR:", e)
            traceback.print_exc()
            raise

        # ---------- POST PROCESS ----------
        try:
            print("Starting post-processing...")

            now = datetime.now(timezone.utc)
            print("Time:", now)

            d = self.client.scan()

            if d is None:
                print("No market data")
                return

            c = self.con()
            q = c.cursor()

            q.execute("""
                SELECT id, symbol, entry, stop, target, qty 
                FROM trades 
                WHERE status='OPEN'
            """)

            rows = q.fetchall()
            print(f"Open trades: {len(rows)}")

            for tid, sym, entry, stop, target, qty in rows:
                try:
                    z = d[d.Symbol == sym]

                    if z.empty:
                        continue

                    price = float(z.iloc[0]["Close"])

                    if price <= stop or price >= target:
                        print(f"Closing trade {sym} at {price}")

                        q.execute(
                            "UPDATE trades SET status='CLOSED' WHERE id=%s",
                            (tid,)
                        )

                except Exception as inner_e:
                    print(f"Trade error {sym}:", inner_e)

            c.commit()

        except Exception as e:
            print("Post-processing error:", e)

        finally:
            try:
                q.close()
                c.close()
            except:
                pass

    # ---------- SAVE METHOD ----------
    def save(self, signals):
        # You MUST already have logic — this is placeholder
        print("Saving signals...")

        c = self.con()
        q = c.cursor()

        for s in signals:
            try:
                q.execute(
                    "INSERT INTO trades(symbol, entry, stop, target, qty, status) VALUES(%s,%s,%s,%s,%s,'OPEN')",
                    (s['symbol'], s['entry'], s['stop'], s['target'], s['qty'])
                )
            except Exception as e:
                print("Insert error:", e)

        c.commit()
        q.close()
        c.close()
