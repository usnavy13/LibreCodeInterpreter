-- heading-unnumbered-v4.lua
-- Filtre Pandoc pour titres non numérotés dans Word
-- IMPORTANT: S'exécute AVANT --shift-heading-level-by
-- Donc ## = level 2 dans ce filtre (même si ça devient Heading 1 après shift)

-- Usage dans markdown: ## Mon Titre {.unnumbered}
-- Résultat Word: "Titre 1 sans numérotation" (car level 2 - 1 = 1)

-- Correspondance : niveau AVANT shift -> style Word
-- level 2 dans MD (##) -> devient Heading 1 -> on veut "Titre 1 sans numérotation"
-- level 3 dans MD (###) -> devient Heading 2 -> on veut "Titre 2 sans numérotation"
local styles_sans_num = {
  [2] = "Titre 1 sans numérotation",  -- ## devient H1
  [3] = "Titre 2 sans numérotation",  -- ### devient H2
  [4] = "Titre 3 sans numérotation"   -- #### devient H3
}

function Header(el)
  -- Debug: décommenter pour voir les niveaux
  -- io.stderr:write("Header level: " .. el.level .. ", classes: " .. table.concat(el.classes, ", ") .. "\n")
  
  -- Vérifie si classe "unnumbered" présente
  local is_unnumbered = false
  for _, class in ipairs(el.classes) do
    if class == "unnumbered" then
      is_unnumbered = true
      break
    end
  end
  
  if is_unnumbered then
    local style_name = styles_sans_num[el.level]
    if style_name then
      -- Transforme en Div avec custom-style
      local div = pandoc.Div(pandoc.Para(el.content))
      div.attributes["custom-style"] = style_name
      return div
    end
  end
  
  return el
end
