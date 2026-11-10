# Identidade visual

O SysOrdem Imobil usa a identidade padrão dos sistemas SysOrdem, a mesma do [sysordem-saude](https://github.com/waguinv/sysordem-saude).

## Cores (tokens em `apps/web/src/app/globals.css`)
| Token | Cor | Uso |
|---|---|---|
| `brand` | `#00C99A` | Verde SysOrdem: ação primária, destaques, ícones ativos |
| `brand-institutional` | `#003B2D` | Barra superior, títulos, painel do login |
| `brand-deep` | `#00291F` | Texto sobre o verde |
| `brand-surface` | `#F0F7F4` | Fundo das áreas de conteúdo |
| `brand-muted` | `#52665F` | Texto secundário, ícones inativos |
| `destructive` | `#D92D20` | Exclusões e erros |

Status de lote: disponível `#00C99A`, reservado `#F5C542`, vendido `#003B2D`, bloqueado `#9AA5A1`.

## Tipografia e forma
- Fonte **Inter** (`next/font`), raio base `0.625rem`, cartões brancos com borda `#DBE7E2` e sombra leve.
- Layout de app: menu lateral branco (264 px) com logo, grupos recolhíveis e botão "Suporte" verde; barra superior institucional `#003B2D`; gaveta lateral no celular.
- Login: painel institucional à esquerda com a logo invertida e o lema "… *em ordem*", formulário em cartão à direita.

## Logo
`apps/web/public/brand/`: `logo-horizontal-{color,reverse,white}.svg` e `simbolo-{color,reverse,white}.svg`.
Símbolo e palavra SYSORDEM são os contornos oficiais do kit; a segunda linha "IMOBIL" é gerada com Inter SemiBold na mesma posição, altura e espaçamento de "SAÚDE" (`apps/web/scripts/gerar_logo.py`). Se o kit da marca tiver a fonte original, regere a segunda linha com ela.
