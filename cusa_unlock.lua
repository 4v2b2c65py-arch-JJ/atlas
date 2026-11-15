-- cusa_unlock.lua — Lua port of the ATLAS auto-unlock command.
-- Same API shape, under the "cusa" lore registry key.
-- Usage: lua cusa_unlock.lua "<title>" [profile]
-- Reads data/lore/registry.json, resolves the lore key, emits an
-- unlock record JSON to stdout. Download/comment stay in the
-- Python hub — Lua handles query + registry + record shaping.

local title = arg[1] or ""
local profile = arg[2] or "default"

local function read_file(p)
  local f = io.open(p, "r")
  if not f then return nil end
  local s = f:read("*a")
  f:close()
  return s
end

-- minimal JSON parse for our own registry shape (no external deps)
local function lore_key()
  local reg = read_file("data/lore/registry.json")
  if not reg then return "default" end
  local game_terms = {"ps4","ps5","ps3","ps2","psx","dump","game","iso","rom","pkg"}
  local tl = title:lower()
  for _, w in ipairs(game_terms) do
    if tl:find(w, 1, true) and reg:find('"cusa"', 1, true) then
      return "cusa"
    end
  end
  return "default"
end

local key = lore_key()
local rec = string.format(
  '{"ts":%d,"event":"unlock_query","title":%s,' ..
  '"lore_key":"%s","profile":"%s","via":"lua"}',
  os.time(), -- title needs JSON escaping; keep it simple/safe
  string.format("%q", title):gsub("\\'", "'"),
  key, profile)

-- append to the profile's unlock log (same file Python writes)
local dir = "data/profiles/" .. profile
os.execute('mkdir -p "' .. dir .. '/unlocks"')
local lf = io.open(dir .. "/unlocks.jsonl", "a")
if lf then lf:write(rec .. "\n") lf:close() end

print(rec)
