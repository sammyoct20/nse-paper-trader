from datetime import datetime, timezone


class PaperEngine:

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

    def run_once(self):
        try:
            print("=== ENGINE START ===")

            # --- Scan ---
            signals = self.scan_market()

            if signals is None:
                print("WARNING: scan_market returned None")
                signals = []

            print(f"Signals found: {len(signals)}")

            if signals:
                print("Sample signals:", signals[:3])
            else:
                print("No signals found")

            # --- Save ---
            if signals:
                try:
                    self.save(signals)
                    print("Saved to DB")
                except Exception as e:
                    print("Save error:", e)
            else:
                print("Nothing to save")

            print("=== ENGINE END ===")

        except Exception as e:
            print("FATAL ERROR in run_once:", e)
            raise

        # --- Post-processing (SAFE BLOCK) ---
        try:
            cfg = self.config()
            now = datetime.now(timezone.utc)

            print("Post-processing at:", now)

            d = self.client.scan()  # market data
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
                    print(f"Trade processing error for {sym}:", inner_e)

            c.commit()

        except Exception as e:
            print("Post-processing error:", e)

        finally:
            try:
                q.close()
                c.close()
            except:
                pass
