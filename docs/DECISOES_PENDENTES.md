# Decisões a confirmar com o cliente
1. A empresa atua como loteadora, intermediadora ou ambas?
2. Tipo e localização dos empreendimentos; requisitos municipais aplicáveis.
3. Forma de pagamento ao proprietário da área e base da comissão. *Fase 3b adotou (hipótese): repasse de % configurável por empreendimento sobre cada parcela recebida.*
4. Base e momento de cálculo da comissão dos corretores. *Fase 3b adotou (configurável por empresa): padrão 5% sobre cada valor de parcela recebido; alternativa: sobre o total na assinatura; % próprio por corretor.*
5. Regras para juros, multa, correção, antecipação, renegociação e distrato. *Fase 3a adotou (a validar com jurídico/contábil): multa única + mora pro rata die (mês de 30 dias) sobre a parte paga em atraso; correção monetária ainda não aplicada; antecipação, renegociação e distrato ficam para a 3c.*
6. Banco/PSP prioritário e meios de cobrança do MVP.
7. Formato da planta (DWG, KML, GeoJSON, PDF, imagem etc.). *Hoje: mapa esquemático por quadra; `lots.geometry` (GeoJSON) reservado.*
8. Quantidade de empresas, empreendimentos, lotes, usuários e contratos para migração.
9. Relatórios exigidos pelo contador.
10. Domínio do site e provedores de e-mail/WhatsApp/assinatura eletrônica.
11. Confirmar as transições de status de lote adotadas na Fase 1 (ver ARQUITETURA.md) e quem pode vender sem reserva prévia.
12. Proprietário da área: um por empreendimento (adotado) ou vários com participação percentual?
13. Validade padrão da reserva (adotado: 72 h; máx. 30 dias) — confirmar e definir se varia por empreendimento.
14. Reserva exige sinal/pagamento? Hoje é só bloqueio com prazo; sinal entra com o financeiro (Fase 3).
15. Corretor deve ver/reservar só os próprios clientes ou todos os lotes/clientes da empresa? *Adotado na Fase 2c (hipótese a confirmar): só os próprios leads, clientes, propostas e reservas; mapa e lotes visíveis a todos. Corretor parceiro sem acesso ao sistema continua sendo só um cadastro.*
16. ~~Política de juros~~ **Decidido:** cada empresa configura sua política (padrões e limites) em Configurações → Política de contratos. Padrões do sistema (multa 2%, mora 1% a.m.) seguem como hipótese a validar com jurídico/contábil.
17. ~~Price ou SAC~~ **Decidido:** a empresa define os sistemas permitidos e o padrão; em cada contrato, empresa e cliente escolhem. Ajuste de centavos na última parcela (adotado para a Fase 3).
18. Assinatura eletrônica gratuita e integrável — **recomendação: Documenso (Community Edition, open source AGPL-3.0, auto-hospedado, com API e webhooks)**, rodando junto no Docker Compose. Alternativas SaaS com plano grátis (ZapSign, Autentique) limitam documentos/mês e, pelas fontes consultadas, exigem plano pago para usar a API em produção. Confirmar a escolha e validar com jurídico o tipo de assinatura exigido (eletrônica avançada × ICP-Brasil) para o registro dos contratos.
19. Validade padrão da proposta (adotado: 7 dias; máx. 90) e se proposta aceita deve recusar automaticamente as concorrentes no mesmo lote (hoje elas ficam abertas e não podem mais ser aceitas).
20. Quais origens de lead usar e se haverá captura automática (formulário do site, WhatsApp) — prevista com o site público (Fase 5).
21. Fonte e periodicidade dos índices de correção (IPCA/IGP-M/INCC): manual mensal, arquivo ou integração (ex.: séries do Banco Central)? Aplicar anualmente ou mensalmente?
22. Vencimento da entrada: hoje vence no dia da assinatura — confirmar se deve ser configurável.
23. O perfil comercial deve consultar a situação de pagamento dos clientes? (hoje só owner, admin e financeiro veem o financeiro)
24. Retenção de impostos (IR/ISS/INSS) sobre comissões e repasses e emissão de recibos/RPA — hoje a conta a pagar é pelo valor bruto.
25. Corretor deve ver o próprio extrato de comissões no sistema? (hoje só o financeiro vê)
