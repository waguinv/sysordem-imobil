# Decisões a confirmar com o cliente
1. A empresa atua como loteadora, intermediadora ou ambas?
2. Tipo e localização dos empreendimentos; requisitos municipais aplicáveis.
3. Forma de pagamento ao proprietário da área e base da comissão.
4. Base e momento de cálculo da comissão dos corretores.
5. Regras para juros, multa, correção, antecipação, renegociação e distrato.
6. Banco/PSP prioritário e meios de cobrança do MVP.
7. Formato da planta (DWG, KML, GeoJSON, PDF, imagem etc.). *Hoje: mapa esquemático por quadra; `lots.geometry` (GeoJSON) reservado.*
8. Quantidade de empresas, empreendimentos, lotes, usuários e contratos para migração.
9. Relatórios exigidos pelo contador.
10. Domínio do site e provedores de e-mail/WhatsApp/assinatura eletrônica.
11. Confirmar as transições de status de lote adotadas na Fase 1 (ver ARQUITETURA.md) e quem pode vender sem reserva prévia.
12. Proprietário da área: um por empreendimento (adotado) ou vários com participação percentual?
13. Validade padrão da reserva (adotado: 72 h; máx. 30 dias) — confirmar e definir se varia por empreendimento.
14. Reserva exige sinal/pagamento? Hoje é só bloqueio com prazo; sinal entra com o financeiro (Fase 3).
15. Corretor deve ver/reservar só os próprios clientes (proposta para a Fase 2b) ou todos os lotes/clientes da empresa?
16. ~~Política de juros~~ **Decidido:** cada empresa configura sua política (padrões e limites) em Configurações → Política de contratos. Padrões do sistema (multa 2%, mora 1% a.m.) seguem como hipótese a validar com jurídico/contábil.
17. ~~Price ou SAC~~ **Decidido:** a empresa define os sistemas permitidos e o padrão; em cada contrato, empresa e cliente escolhem. Ajuste de centavos na última parcela (adotado para a Fase 3).
18. Assinatura eletrônica gratuita e integrável — **recomendação: Documenso (Community Edition, open source AGPL-3.0, auto-hospedado, com API e webhooks)**, rodando junto no Docker Compose. Alternativas SaaS com plano grátis (ZapSign, Autentique) limitam documentos/mês e, pelas fontes consultadas, exigem plano pago para usar a API em produção. Confirmar a escolha e validar com jurídico o tipo de assinatura exigido (eletrônica avançada × ICP-Brasil) para o registro dos contratos.
