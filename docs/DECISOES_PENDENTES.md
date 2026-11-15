# Decisões a confirmar com o cliente
1. A empresa atua como loteadora, intermediadora ou ambas?
2. Tipo e localização dos empreendimentos; requisitos municipais aplicáveis.
3. Forma de pagamento ao proprietário da área e base da comissão. *Fase 3b adotou (hipótese): repasse de % configurável por empreendimento sobre cada parcela recebida.*
4. Base e momento de cálculo da comissão dos corretores. *Fase 3b adotou (configurável por empresa): padrão 5% sobre cada valor de parcela recebido; alternativa: sobre o total na assinatura; % próprio por corretor.*
5. Regras para juros, multa, correção, antecipação, renegociação e distrato. *Fase 3a adotou (a validar com jurídico/contábil): multa única + mora pro rata die (mês de 30 dias) sobre a parte paga em atraso; correção monetária ainda não aplicada; Fase 3c adotou: renegociação de todo o saldo em aberto (encargos opcionais, desconto em R$) e distrato com retenção padrão de 10% do contrato limitada ao pago (Lei 13.786/2018 como referência) — validar com jurídico. Antecipação com desconto ainda não implementada.*
6. Banco/PSP prioritário e meios de cobrança do MVP. *Fase 4a entregou o adaptador e um PSP **sandbox** (Pix e boleto simulados, webhooks assinados). O PSP real (4c) depende desta escolha: custo por Pix/boleto, Pix com vencimento (cobv) e multa/juros calculados pelo PSP, boleto híbrido, API de cancelamento e de extrato (para a conciliação da 4b).*
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
20. Quais origens de lead usar e se haverá captura automática (formulário do site, WhatsApp) — prevista com o site público (Fase 5). *Fase 5a: formulário do site cria lead com origem "Site" e consentimento.*
21. Fonte e periodicidade dos índices de correção (IPCA/IGP-M/INCC): manual mensal, arquivo ou integração (ex.: séries do Banco Central)? Aplicar anualmente ou mensalmente?
22. Vencimento da entrada: hoje vence no dia da assinatura — confirmar se deve ser configurável.
23. O perfil comercial deve consultar a situação de pagamento dos clientes? (hoje só owner, admin e financeiro veem o financeiro)
24. Retenção de impostos (IR/ISS/INSS) sobre comissões e repasses e emissão de recibos/RPA — hoje a conta a pagar é pelo valor bruto.
25. Corretor deve ver o próprio extrato de comissões no sistema? (hoje só o financeiro vê)
26. No distrato, estornar comissões e repasses já lançados? E a retenção deve considerar fruição/IPTU/condomínio (art. 32-A da Lei 6.766)?
27. Prazo padrão da devolução no distrato (hoje 30 dias, editável) e se pode ser parcelada.
28. Relatórios exigidos pelo contador (decisão 9): os três atuais (fluxo, inadimplência, vendas) bastam? Precisa de DRE/regime de competência ou integração com sistema contábil?
29. Cobrança emitida automaticamente (ex.: X dias antes do vencimento, com envio por e-mail/WhatsApp) ou só sob demanda, como hoje?
30. Pagamento recebido a mais ou em parcela já quitada (divergência): devolver ao pagador ou abater das próximas parcelas?
31. Conciliação: o PSP credita o valor líquido de tarifas? Se sim, lançar a tarifa como conta paga automaticamente na conciliação ou só ignorar a diferença?
32. Integração direta com o banco (Open Finance / API de extrato) ou basta importar OFX/CSV? Quais bancos a empresa usa?
33. Site público: quem publica (hoje owner/admin; incluir o comercial?), mostrar preços por padrão, e mostrar "reservado" separado de "vendido" (hoje ambos aparecem como indisponível)?
34. Leads do site: distribuir automaticamente para corretores (rodízio) ou o comercial distribui? Fotos, planta e vídeo no site — onde guardar (armazenamento privado com URL temporária)?
35. Portal do comprador: enviar convite e "esqueci a senha" por e-mail/WhatsApp (depende da decisão 10)? O comprador pode atualizar telefone/endereço pelo portal? Disponibilizar contrato assinado e boleto em PDF?
36. Informe de pagamentos: formato exigido pelo contador (só valores pagos no ano, como hoje, ou também saldo devedor em 31/12 e CNPJ da empresa)?
37. Planos SaaS: preços, limites (empreendimentos, lotes, usuários), dias de teste (14), carência (10 dias) e se recursos (portal, site, conciliação) variam por plano. Hoje: Essencial R$ 197 (1/300/3), Profissional R$ 397 (5/2.000/10), Empresarial R$ 797 (ilimitado).
38. Console do operador SaaS (SysOrdem): ver empresas, conceder cortesia/desconto, estornar fatura, encerrar conta e exportar dados (LGPD). Quem opera e com que acesso?
39. Emissão de nota fiscal de serviço da assinatura e e-mail de cobrança/recibo (provedor de e-mail — decisão 10).
40. Termos de uso e política de privacidade: textos jurídicos a redigir (hoje só o aceite é registrado).
