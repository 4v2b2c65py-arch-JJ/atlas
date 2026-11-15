"""DNA dataset connector — fetch NCBI nucleotide records, parse
FASTA, run motif/GC/codon analysis, expand the record store.

Sources: NCBI efetch (no key needed for small pulls).
Stores:  data/dna/*.fasta   raw sequences (verbatim)
         data/dna/records.jsonl  analysis records
         data/dna/dna.gguf       packed store
"""

import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "dna"
RECORDS = OUT / "records.jsonl"
GGUF = OUT / "dna.gguf"
UA = {"User-Agent": "atlas-dna/1.0"}

CODON_TABLE = {
    'TTT':'F','TTC':'F','TTA':'L','TTG':'L','TCT':'S','TCC':'S',
    'TCA':'S','TCG':'S','TAT':'Y','TAC':'Y','TAA':'*','TAG':'*',
    'TGT':'C','TGC':'C','TGA':'*','TGG':'W','CTT':'L','CTC':'L',
    'CTA':'L','CTG':'L','CCT':'P','CCC':'P','CCA':'P','CCG':'P',
    'CAT':'H','CAC':'H','CAA':'Q','CAG':'Q','CGT':'R','CGC':'R',
    'CGA':'R','CGG':'R','ATT':'I','ATC':'I','ATA':'I','ATG':'M',
    'ACT':'T','ACC':'T','ACA':'T','ACG':'T','AAT':'N','AAC':'N',
    'AAA':'K','AAG':'K','AGT':'S','AGC':'S','AGA':'R','AGG':'R',
    'GTT':'V','GTC':'V','GTA':'V','GTG':'V','GCT':'A','GCC':'A',
    'GCA':'A','GCG':'A','GAT':'D','GAC':'D','GAA':'E','GAG':'E',
    'GGT':'G','GGC':'G','GGA':'G','GGG':'G'}

# built-in motif seeds — expand per dataset
MOTIFS = ["ATG", "TAA", "TAG", "TGA", "GATC", "GAATTC",
          "GGATCC", "AAGCTT", "GCGATCGC"]


def fetch_fasta(acc: str, rettype: str = "fasta") -> str:
    """NCBI efetch — returns raw FASTA text."""
    u = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
         f"efetch.fcgi?db=nucleotide&id={acc}"
         f"&rettype={rettype}&retmode=text")
    req = urllib.request.Request(u, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode()


def parse_fasta(text: str) -> dict:
    """FASTA -> {header, seq, length, alphabet}."""
    lines = text.strip().splitlines()
    header = lines[0].lstrip(">").strip() if lines else ""
    seq = "".join(l.strip() for l in lines[1:] if l.strip())
    return {"header": header, "seq": seq.upper(),
            "length": len(seq),
            "alphabet": sorted(set(seq))}


def gc_content(seq: str) -> float:
    if not seq:
        return 0.0
    gc = sum(1 for b in seq if b in "GC")
    return round(gc / len(seq) * 100, 2)


def base_composition(seq: str) -> dict:
    n = len(seq) or 1
    return {b: round(seq.count(b) / n * 100, 2)
            for b in "ACGT"}


def find_motifs(seq: str, motifs: list[str] | None = None,
                window: int = 0) -> list[dict]:
    """Locate motif occurrences; window>0 restricts first N bp."""
    seq = seq.upper()
    scan = seq[:window] if window else seq
    out = []
    for m in (motifs or MOTIFS):
        i = scan.find(m)
        while i != -1:
            out.append({"motif": m, "pos": i})
            i = scan.find(m, i + 1)
    return sorted(out, key=lambda x: x["pos"])


def codon_stats(seq: str, limit: int = 2000) -> dict:
    """Amino-acid rough composition over the first `limit` bp
    (frame 0, stops included as *)."""
    seq = seq[:limit]
    counts = {}
    for i in range(0, len(seq) - 2, 3):
        aa = CODON_TABLE.get(seq[i:i + 3], "?")
        counts[aa] = counts.get(aa, 0) + 1
    total = sum(counts.values()) or 1
    return {"frame": 0, "codons": total,
            "aa": {k: round(v / total * 100, 1)
                   for k, v in
                   sorted(counts.items(),
                          key=lambda kv: -kv[1])}}


def analyze(acc: str, save_raw: bool = True) -> dict:
    """Fetch + parse + analyze one accession; stores raw FASTA
    verbatim and a derived record."""
    raw = fetch_fasta(acc)
    rec = parse_fasta(raw)
    if save_raw:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{acc}.fasta").write_text(raw)
    motifs = find_motifs(rec["seq"])
    derived = {"ts": time.time(), "kind": "dna_record",
               "accession": acc, "header": rec["header"],
               "length": rec["length"],
               "alphabet": rec["alphabet"],
               "gc_pct": gc_content(rec["seq"]),
               "base_comp": base_composition(rec["seq"]),
               "motifs": motifs[:50], "motif_count": len(motifs),
               "codons": codon_stats(rec["seq"]),
               "derived": True}
    OUT.mkdir(parents=True, exist_ok=True)
    with RECORDS.open("a") as f:
        f.write(json.dumps(derived, ensure_ascii=False) + "\n")
    return derived


def expand_datasets(accessions: list[str]) -> dict:
    """Batch fetch — expand the store with as many as given."""
    done, failed = [], []
    for acc in accessions:
        try:
            done.append(analyze(acc)["accession"])
        except Exception as e:
            failed.append({"accession": acc,
                           "error": str(e)[:120]})
    return {"fetched": done, "failed": failed,
            "records": str(RECORDS)}


def pack_gguf() -> dict:
    import namescrape
    recs = []
    if RECORDS.exists():
        for line in RECORDS.open():
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    return namescrape.write_gguf(recs, path=GGUF)


def status() -> dict:
    def count(p):
        return sum(1 for _ in p.open()) if p.exists() else 0
    fasta = sorted(p.name for p in OUT.glob("*.fasta")) \
        if OUT.exists() else []
    return {"records": count(RECORDS), "fasta": fasta,
            "motifs": len(MOTIFS), "codon_table": len(CODON_TABLE)}
