// ATLAS BitTorrent resolver — downloads ONLY the largest video file
// from a magnet/torrent, streams it to <outdir>/video.<ext>, exits 0.
// Usage: node bt_download.mjs <magnet-or-torrent> <outdir> [timeout_s]
import webtorrent from "webtorrent";
import fs from "node:fs";
import path from "node:path";

const [magnet, outdir, tmo] = process.argv.slice(2);
if (!magnet || !outdir) {
  console.error("usage: bt_download.mjs <magnet> <outdir> [timeout_s]");
  process.exit(64);
}
fs.mkdirSync(outdir, { recursive: true });
const timeout = (Number(tmo) || 300) * 1000;
const VID = /\.(mp4|mkv|webm|mov|avi|m4v|ts)$/i;

// WebTorrent's default DHT bootstrap is nearly empty — inject known
// nodes so the routing table actually populates.
const DHT_BOOTSTRAP = [
  { host: "router.bittorrent.com", port: 6881 },
  { host: "router.utorrent.com", port: 6881 },
  { host: "dht.transmissionbt.com", port: 6881 },
  { host: "router.bitcomet.com", port: 6881 },
  { host: "dht.aelitis.com", port: 6881 },
  { host: "dht.libtorrent.org", port: 25401 },
];
const client = new webtorrent({
  dht: { bootstrap: DHT_BOOTSTRAP },
  tracker: true, lsd: true,
});
const bail = (code, msg) => { console.log(msg); client.destroy(); process.exit(code); };
setTimeout(() => bail(3, "TIMEOUT"), timeout);

client.add(magnet, { path: outdir }, (torrent) => {
  torrent.on("error", (e) => bail(4, "TORRENT_ERROR " + e.message));
  const prog = setInterval(() => {
    console.log("PROGRESS peers=" + torrent.numPeers +
      " downloaded=" + (torrent.downloaded / 1e6).toFixed(1) + "MB" +
      " speed=" + (torrent.downloadSpeed / 1e3).toFixed(0) + "kB/s" +
      " files=" + torrent.files.length);
  }, 10000);
  torrent.on("done", () => clearInterval(prog));
  const vids = torrent.files.filter((f) => VID.test(f.name));
  const pool = vids.length ? vids : torrent.files;
  const file = pool.sort((a, b) => b.length - a.length)[0];
  if (!file) return bail(2, "NOFILES");
  const ext = path.extname(file.name) || ".mp4";
  const dest = path.join(outdir, "video" + ext);
  console.log("SELECT", file.name, file.length, "->", dest);
  // webtorrent 3.x: createReadStream pulls only this file's pieces
  const ws = fs.createWriteStream(dest);
  file.createReadStream()
    .on("data", () => {})
    .pipe(ws)
    .on("finish", () => {
      const sz = fs.statSync(dest).size;
      if (sz < 50000) return bail(5, "TOO_SMALL " + sz);
      console.log("DONE", dest, sz);
      client.destroy();
      process.exit(0);
    })
    .on("error", (e) => bail(6, "WRITE_ERROR " + e.message));
});
client.on("error", (e) => bail(4, "CLIENT_ERROR " + e.message));
