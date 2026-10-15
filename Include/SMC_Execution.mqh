#ifndef SMC_EXECUTION_MQH
#define SMC_EXECUTION_MQH

#include <Trade/Trade.mqh>
#include <SMC_Config.mqh>
#include <SMC_RiskManager.mqh>
#include <SMC_TradeLedger.mqh>
#include <SMC_Logger.mqh>
#include <SMC_SetupStateMachine.mqh>

class CSMCExecution
  {
private:
   string m_symbol;
   long   m_magic;
   bool   m_unresolved_request;
   ulong  m_last_request_order;

   bool IsPendingOrderType(const ENUM_ORDER_TYPE type) const
     {
      return (type==ORDER_TYPE_BUY_LIMIT || type==ORDER_TYPE_SELL_LIMIT ||
              type==ORDER_TYPE_BUY_STOP || type==ORDER_TYPE_SELL_STOP ||
              type==ORDER_TYPE_BUY_STOP_LIMIT || type==ORDER_TYPE_SELL_STOP_LIMIT);
     }

   bool FindOwnedPending(const long magic,const string symbol,ulong &ticket) const
     {
      ticket=0;
      for(int index=0;index<OrdersTotal();index++)
        {
         const ulong current=OrderGetTicket(index);
         if(current==0)
            continue;
         if(OrderGetString(ORDER_SYMBOL)==symbol && OrderGetInteger(ORDER_MAGIC)==magic &&
            IsPendingOrderType((ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE)))
           {
            ticket=current;
            return true;
           }
        }
      return false;
     }

   bool FindOwnedPosition(const long magic,const string symbol,ulong &identifier) const
     {
      identifier=0;
      for(int index=0;index<PositionsTotal();index++)
        {
         const ulong ticket=PositionGetTicket(index);
         if(ticket==0)
            continue;
         if(PositionGetString(POSITION_SYMBOL)==symbol && PositionGetInteger(POSITION_MAGIC)==magic)
           {
            identifier=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
            return true;
           }
        }
      return false;
     }

   bool RemoveOrder(const ulong ticket,MqlTradeResult &result) const
     {
      MqlTradeRequest request={};
      request.action=TRADE_ACTION_REMOVE;
      request.order=ticket;
      if(!OrderSend(request,result))
         return false;
      return (result.retcode==TRADE_RETCODE_DONE || result.retcode==TRADE_RETCODE_PLACED);
     }

public:
                     CSMCExecution()
     {
      m_symbol="";
      m_magic=0;
      m_unresolved_request=false;
      m_last_request_order=0;
     }

   bool Init(const string symbol,const long magic)
     {
      m_symbol=symbol;
      m_magic=magic;
      m_unresolved_request=false;
      m_last_request_order=0;
      return (symbol!="" && magic>0);
     }

   bool HasUnresolvedRequest() const { return m_unresolved_request; }

   bool RecoverAtStartup(SMC_SETUP &setup,CSMCSetupStateMachine &states,
                         CSMCTradeLedger &ledger,CSMCLogger &logger)
     {
      // Existing open positions are accepted as owned and remain protected by their server SL/TP.
      // Unknown pending setups are cancelled because their complete signal state cannot be proven.
      ulong position_id=0;
      if(FindOwnedPosition(m_magic,m_symbol,position_id))
        {
         datetime now=TimeTradeServer();
         if(now<=0) now=TimeCurrent();
         states.RecoverPosition(setup,position_id,now);
         logger.Write(setup,"startup_position_reconciled","STARTUP",0.0,0.0,0,
                      "owned_position_found_protective_orders_retained");
        }

      for(int index=OrdersTotal()-1;index>=0;index--)
        {
         const ulong ticket=OrderGetTicket(index);
         if(ticket==0 || OrderGetString(ORDER_SYMBOL)!=m_symbol ||
            OrderGetInteger(ORDER_MAGIC)!=m_magic ||
            !IsPendingOrderType((ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE)))
            continue;
         MqlTradeResult result={};
         if(!RemoveOrder(ticket,result))
           {
            m_unresolved_request=true;
            setup.setup_id=StringFormat("RECOVERED-ORDER-%I64d",(long)ticket);
            setup.state=SMC_ORDER_PENDING;
            setup.order_ticket=ticket;
            logger.Write(setup,"startup_order_cancel_pending","ORDER_PENDING",0.0,0.0,
                         (int)result.retcode,"setup_state_unavailable_or_unverifiable");
            return false;
           }
         SMC_SETUP recovered_setup;
         ZeroMemory(recovered_setup);
         recovered_setup.setup_id="recovered-order";
         recovered_setup.state=SMC_INVALIDATED;
         logger.Write(recovered_setup,"startup_order_cancelled","STARTUP",0.0,0.0,
                      (int)result.retcode,"unverifiable_setup_not_recreated");
        }
      m_unresolved_request=false;
      return ledger.IsReliable();
     }

   bool SubmitMidpointLimit(SMC_SETUP &setup,CSMCSetupStateMachine &states,
                            CSMCRiskManager &risk,CSMCTradeLedger &ledger,
                            CSMCLogger &logger,const double risk_percent)
     {
      if(setup.state!=SMC_WAIT_RETRACE_OR_ORDER || setup.setup_id=="" ||
         m_unresolved_request || !ledger.IsReliable() || ledger.HasOwnedPosition() ||
         ledger.HasOwnedPendingOrder() || !risk.CanOpenTrade(ledger.ConsecutiveLosses()))
        {
         logger.Write(setup,"order_rejected","WAIT_RETRACE_OR_ORDER",0.0,risk_percent,0,
                      "state_risk_or_owned_order_position_gate");
         return false;
        }
      if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED) ||
         !AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))
        {
         logger.Write(setup,"order_rejected","WAIT_RETRACE_OR_ORDER",0.0,risk_percent,0,
                      "terminal_mql_or_account_trading_disabled");
         return false;
        }
      const long trade_mode=SymbolInfoInteger(m_symbol,SYMBOL_TRADE_MODE);
      if(trade_mode==SYMBOL_TRADE_MODE_DISABLED || trade_mode==SYMBOL_TRADE_MODE_CLOSEONLY)
        {
         logger.Write(setup,"order_rejected","WAIT_RETRACE_OR_ORDER",0.0,risk_percent,0,
                      "symbol_trade_mode_disallows_new_entries");
         return false;
        }

      MqlTick tick;
      if(!SymbolInfoTick(m_symbol,tick) || tick.bid<=0.0 || tick.ask<tick.bid)
        {
         logger.Write(setup,"order_rejected","WAIT_RETRACE_OR_ORDER",0.0,risk_percent,0,
                      "live_bid_ask_unavailable_or_invalid");
         return false;
        }
      const double spread=tick.ask-tick.bid;
      if(spread>SMC_MAX_SPREAD_PRICE)
        {
         logger.Write(setup,"order_rejected","WAIT_RETRACE_OR_ORDER",spread,risk_percent,0,
                      "spread_exceeds_0.50_price_units");
         return false;
        }

      double tick_size=0.0,point=0.0;
      long digits=0,stops_points=0,freeze_points=0,filling_flags=0,execution_mode=0;
      if(!SymbolInfoDouble(m_symbol,SYMBOL_TRADE_TICK_SIZE,tick_size) || tick_size<=0.0 ||
         !SymbolInfoDouble(m_symbol,SYMBOL_POINT,point) || point<=0.0 ||
         !SymbolInfoInteger(m_symbol,SYMBOL_DIGITS,digits))
        {
         logger.Write(setup,"order_rejected","WAIT_RETRACE_OR_ORDER",spread,risk_percent,0,
                      "symbol_price_properties_unavailable");
         return false;
        }
      stops_points=SymbolInfoInteger(m_symbol,SYMBOL_TRADE_STOPS_LEVEL);
      freeze_points=SymbolInfoInteger(m_symbol,SYMBOL_TRADE_FREEZE_LEVEL);
      filling_flags=SymbolInfoInteger(m_symbol,SYMBOL_FILLING_MODE);
      execution_mode=SymbolInfoInteger(m_symbol,SYMBOL_TRADE_EXEMODE);
      const ENUM_ORDER_TYPE order_type=(setup.direction==SMC_DIRECTION_BUY ? ORDER_TYPE_BUY_LIMIT : ORDER_TYPE_SELL_LIMIT);

      const double raw_entry=(setup.fvg_lower+setup.fvg_upper)*0.5;
      setup.entry_price=NormalizeDouble(SMCRoundToTick(raw_entry,tick_size),(int)digits);
      //--- V2: SL/TP sudah final dihitung sebelum sampai sini ------------------
      //| Modul ini hanya memvalidasi dan membulatkan ke tick. Nilai TIDAK boleh
      //| dihitung ulang dari rumus B3: dulu baris ini menimpa stop yang sudah
      //| diperlebar sehingga lantai risiko V2 hilang tepat sebelum order dikirim.
      if(!(setup.stop_loss>0.0 && setup.take_profit>0.0))
        {
         logger.Write(setup,"order_rejected","WAIT_RETRACE_OR_ORDER",spread,risk_percent,0,
                      "v2_final_protection_levels_missing");
         return false;
        }
      setup.stop_loss=NormalizeDouble(SMCRoundToTick(setup.stop_loss,tick_size),(int)digits);
      setup.take_profit=NormalizeDouble(SMCRoundToTick(setup.take_profit,tick_size),(int)digits);

      if(setup.direction==SMC_DIRECTION_BUY && !(setup.stop_loss<setup.entry_price &&
         setup.entry_price<setup.take_profit && setup.entry_price<tick.ask))
        {
         logger.Write(setup,"order_rejected","WAIT_RETRACE_OR_ORDER",spread,risk_percent,0,
                      "buy_limit_or_sl_tp_side_invalid");
         return false;
        }
      if(setup.direction==SMC_DIRECTION_SELL && !(setup.stop_loss>setup.entry_price &&
         setup.entry_price>setup.take_profit && setup.entry_price>tick.bid))
        {
         logger.Write(setup,"order_rejected","WAIT_RETRACE_OR_ORDER",spread,risk_percent,0,
                      "sell_limit_or_sl_tp_side_invalid");
         return false;
        }

      const long minimum_distance_points=MathMax(stops_points,freeze_points);
      const double min_distance=(double)minimum_distance_points*point;
      const double entry_market_distance=(setup.direction==SMC_DIRECTION_BUY ?
                                          tick.ask-setup.entry_price : setup.entry_price-tick.bid);
      if(entry_market_distance<min_distance ||
         MathAbs(setup.entry_price-setup.stop_loss)<(double)stops_points*point ||
         MathAbs(setup.take_profit-setup.entry_price)<(double)stops_points*point)
        {
         logger.Write(setup,"order_rejected","WAIT_RETRACE_OR_ORDER",spread,risk_percent,0,
                      "stops_or_freeze_distance_invalid");
         return false;
        }

      const datetime reference_time=setup.order_reference_time;
      const datetime expiration=reference_time+SMC_PENDING_EXPIRY_SECONDS;
      datetime now=TimeTradeServer();
      if(now<=0) now=TimeCurrent();
      if(reference_time<=0 || now>=expiration)
        {
         logger.Write(setup,"order_rejected","WAIT_RETRACE_OR_ORDER",spread,risk_percent,0,
                      "fvg_reference_expired_or_missing");
         return false;
        }
      setup.order_expiry=expiration;

      if(!risk.CalculateVolume(setup.direction,setup.entry_price,setup.stop_loss,spread,
                               setup.volume,setup.expected_risk))
        {
         logger.Write(setup,"order_rejected","WAIT_RETRACE_OR_ORDER",spread,risk_percent,0,
                      "monetary_risk_or_volume_properties_invalid");
         return false;
        }
      if(!risk.CalculateNetRewardRisk(setup.direction,setup.entry_price,setup.stop_loss,
                                      setup.take_profit,setup.volume,spread,setup.expected_net_rr) ||
         setup.expected_net_rr<SMC_MIN_NET_RR)
        {
         logger.Write(setup,"order_rejected","WAIT_RETRACE_OR_ORDER",spread,risk_percent,0,
                      "net_reward_risk_below_1_to_3_or_costs_unavailable");
         return false;
        }

      double margin=0.0;
      const ENUM_ORDER_TYPE margin_type=(setup.direction==SMC_DIRECTION_BUY ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
      if(!OrderCalcMargin(margin_type,m_symbol,setup.volume,setup.entry_price,margin) ||
         margin>AccountInfoDouble(ACCOUNT_MARGIN_FREE))
        {
         logger.Write(setup,"order_rejected","WAIT_RETRACE_OR_ORDER",spread,risk_percent,0,
                      "margin_calculation_failed_or_insufficient_free_margin");
         return false;
        }

      // Pending orders are not executed at submission; RETURN is the native pending-order policy.
      Print("[SMC] pending filling policy=RETURN symbol_flags=",filling_flags,
            " execution_mode=",execution_mode," symbol=",m_symbol);
      MqlTradeRequest request={};
      MqlTradeCheckResult check={};
      MqlTradeResult result={};
      request.action=TRADE_ACTION_PENDING;
      request.symbol=m_symbol;
      request.magic=(ulong)m_magic;
      request.type=order_type;
      request.volume=setup.volume;
      request.price=setup.entry_price;
      request.sl=setup.stop_loss;
      request.tp=setup.take_profit;
      request.type_filling=ORDER_FILLING_RETURN;
      request.type_time=ORDER_TIME_SPECIFIED;
      request.expiration=expiration;
      request.comment=StringSubstr(setup.setup_id,0,31);

      if(!OrderCheck(request,check))
        {
         logger.Write(setup,"order_rejected","WAIT_RETRACE_OR_ORDER",spread,risk_percent,
                      (int)check.retcode,"ordercheck_failed_"+check.comment);
         return false;
        }
      if(!OrderSend(request,result))
        {
         logger.Write(setup,"order_rejected","WAIT_RETRACE_OR_ORDER",spread,risk_percent,
                      (int)result.retcode,"ordersend_transport_failure_"+result.comment);
         return false;
        }
      if(result.retcode!=TRADE_RETCODE_PLACED && result.retcode!=TRADE_RETCODE_DONE &&
         result.retcode!=TRADE_RETCODE_DONE_PARTIAL)
        {
         logger.Write(setup,"order_rejected","WAIT_RETRACE_OR_ORDER",spread,risk_percent,
                      (int)result.retcode,"trade_server_rejected_"+result.comment);
         return false;
        }

      setup.order_ticket=result.order;
      setup.position_id=0;
      m_last_request_order=result.order;
      ulong position_id=0;
      if(FindOwnedPosition(m_magic,m_symbol,position_id))
        {
         setup.position_id=position_id;
         string before=SMCStateName(setup.state);
         if(states.Transition(setup,SMC_ORDER_PENDING,now,"accepted_midpoint_limit_request"))
            logger.Write(setup,"order_pending",before,spread,risk_percent,
                         (int)result.retcode,"request_accepted_before_immediate_fill");
         before=SMCStateName(setup.state);
         if(states.Transition(setup,SMC_IN_POSITION,now,"order_fill_confirmed_in_terminal"))
            logger.Write(setup,"order_fill_confirmed",before,spread,risk_percent,
                         (int)result.retcode,"owned_position_visible_after_submission");
         return true;
        }
      if(result.order>0 && OrderSelect(result.order) &&
         OrderGetString(ORDER_SYMBOL)==m_symbol && OrderGetInteger(ORDER_MAGIC)==m_magic)
        {
         states.Transition(setup,SMC_ORDER_PENDING,now,"pending_order_confirmed_in_terminal");
         logger.Write(setup,"order_pending","WAIT_RETRACE_OR_ORDER",spread,risk_percent,
                      (int)result.retcode,"pending_order_visible_after_submission");
         return true;
        }

      // Server accepted the request but the order/deal view has not caught up. Block resubmission.
      m_unresolved_request=true;
      logger.Write(setup,"order_state_unresolved","WAIT_RETRACE_OR_ORDER",spread,risk_percent,
                   (int)result.retcode,"accepted_retcode_without_visible_order_or_position");
      return false;
     }

   bool Reconcile(SMC_SETUP &setup,CSMCSetupStateMachine &states,
                  CSMCTradeLedger &ledger,CSMCLogger &logger,const double risk_percent)
     {
      if(setup.state==SMC_ORDER_PENDING)
        {
         ulong identifier=0;
         if(FindOwnedPosition(m_magic,m_symbol,identifier))
           {
            const string before=SMCStateName(setup.state);
            setup.position_id=identifier;
            if(states.Transition(setup,SMC_IN_POSITION,TimeTradeServer(),"partial_or_full_fill_reconciled"))
               logger.Write(setup,"position_reconciled",before,0.0,risk_percent,0,
                            "owned_position_visible_after_pending_order");
            return true;
           }
         if(setup.order_ticket>0 && OrderSelect(setup.order_ticket))
           {
            if(OrderGetString(ORDER_SYMBOL)==m_symbol && OrderGetInteger(ORDER_MAGIC)==m_magic)
               return true;
           }
         if(setup.order_ticket>0 && HistoryOrderSelect(setup.order_ticket))
           {
            const ENUM_ORDER_STATE history_state=(ENUM_ORDER_STATE)HistoryOrderGetInteger(setup.order_ticket,ORDER_STATE);
            if(history_state==ORDER_STATE_EXPIRED)
              {
               states.Transition(setup,SMC_EXPIRED,TimeTradeServer(),"broker_expired_pending_order");
               logger.Write(setup,"order_expired","ORDER_PENDING",0.0,risk_percent,0,"history_order_state_expired");
               return true;
              }
            if(history_state==ORDER_STATE_CANCELED || history_state==ORDER_STATE_REJECTED)
              {
               states.Transition(setup,SMC_INVALIDATED,TimeTradeServer(),"broker_cancelled_or_rejected_pending_order");
               logger.Write(setup,"order_cancelled","ORDER_PENDING",0.0,risk_percent,0,
                            "history_order_state_"+EnumToString(history_state));
               return true;
              }
           }
         m_unresolved_request=true;
         return false;
        }
      if(m_unresolved_request)
        {
         ulong ticket=0;
         if(FindOwnedPending(m_magic,m_symbol,ticket))
           {
            setup.order_ticket=ticket;
            m_unresolved_request=false;
            if(setup.state==SMC_WAIT_RETRACE_OR_ORDER)
               states.Transition(setup,SMC_ORDER_PENDING,TimeTradeServer(),"delayed_pending_order_reconciled");
            logger.Write(setup,"order_pending_reconciled","WAIT_RETRACE_OR_ORDER",0.0,risk_percent,0,
                         "delayed_terminal_order_visible");
            return true;
           }
         ulong position_id=0;
         if(FindOwnedPosition(m_magic,m_symbol,position_id))
           {
            setup.position_id=position_id;
            m_unresolved_request=false;
            if(setup.state==SMC_WAIT_RETRACE_OR_ORDER)
              {
               const string before=SMCStateName(setup.state);
               if(states.Transition(setup,SMC_ORDER_PENDING,TimeTradeServer(),"accepted_limit_request_reconciled"))
                  logger.Write(setup,"order_pending_reconciled",before,0.0,risk_percent,0,
                               "accepted_limit_request_now_visible_as_position");
              }
            const string before_position=SMCStateName(setup.state);
            if(setup.state==SMC_ORDER_PENDING &&
               states.Transition(setup,SMC_IN_POSITION,TimeTradeServer(),"delayed_fill_reconciled"))
               logger.Write(setup,"position_reconciled",before_position,0.0,risk_percent,0,
                            "delayed_terminal_position_visible");
            return true;
           }
         return false;
        }
      return true;
     }

   bool CancelPending(SMC_SETUP &setup,CSMCSetupStateMachine &states,
                      CSMCLogger &logger,const string reason,const bool expired,
                      const double risk_percent)
     {
      if(setup.state!=SMC_ORDER_PENDING || setup.order_ticket==0)
         return false;
      if(!OrderSelect(setup.order_ticket))
         return false; // Reconcile history/position on the next event; do not assume cancellation.
      MqlTradeResult result={};
      if(!RemoveOrder(setup.order_ticket,result))
        {
         logger.Write(setup,"order_cancel_failed","ORDER_PENDING",0.0,risk_percent,
                      (int)result.retcode,reason+";"+result.comment);
         return false;
        }
      const SMC_SETUP_STATE next=(expired ? SMC_EXPIRED : SMC_INVALIDATED);
      const string before=SMCStateName(setup.state);
      if(!states.Transition(setup,next,TimeTradeServer(),reason))
         return false;
      logger.Write(setup,(expired ? "order_expired" : "order_cancelled"),before,0.0,
                   risk_percent,(int)result.retcode,reason);
      return true;
     }

   bool CancelResidualOrder(const SMC_SETUP &setup,CSMCLogger &logger,
                            const string reason,const double risk_percent) const
     {
      if(setup.state!=SMC_IN_POSITION || setup.order_ticket==0 || !OrderSelect(setup.order_ticket))
         return true;
      MqlTradeResult result={};
      if(!RemoveOrder(setup.order_ticket,result))
        {
         logger.Write(setup,"residual_order_cancel_failed","IN_POSITION",0.0,risk_percent,
                      (int)result.retcode,reason+";"+result.comment);
         return false;
        }
      logger.Write(setup,"residual_order_cancelled","IN_POSITION",0.0,risk_percent,
                   (int)result.retcode,reason);
      return true;
     }

   bool EmergencyClose(const ulong position_ticket,const SMC_DIRECTION direction,
                       const double volume,CSMCLogger &logger,const SMC_SETUP &setup,
                       const string reason) const
     {
      if(position_ticket==0 || volume<=0.0 || direction==SMC_DIRECTION_NONE)
         return false;
      MqlTick tick;
      if(!SymbolInfoTick(m_symbol,tick) || tick.bid<=0.0 || tick.ask<tick.bid)
         return false;
      const long filling_flags=SymbolInfoInteger(m_symbol,SYMBOL_FILLING_MODE);
      MqlTradeRequest request={};
      MqlTradeResult result={};
      request.action=TRADE_ACTION_DEAL;
      request.position=position_ticket;
      request.symbol=m_symbol;
      request.magic=(ulong)m_magic;
      request.volume=volume;
      request.type=(direction==SMC_DIRECTION_BUY ? ORDER_TYPE_SELL : ORDER_TYPE_BUY);
      request.price=(direction==SMC_DIRECTION_BUY ? tick.bid : tick.ask);
      if((filling_flags & SYMBOL_FILLING_FOK)!=0)
         request.type_filling=ORDER_FILLING_FOK;
      else if((filling_flags & SYMBOL_FILLING_IOC)!=0)
         request.type_filling=ORDER_FILLING_IOC;
      else
         request.type_filling=ORDER_FILLING_RETURN;
      request.comment="SMC_RISK_OVERRUN";
      if(!OrderSend(request,result) ||
         (result.retcode!=TRADE_RETCODE_DONE && result.retcode!=TRADE_RETCODE_DONE_PARTIAL))
        {
         logger.Write(setup,"emergency_close_failed",SMCStateName(setup.state),0.0,0.0,
                      (int)result.retcode,reason+";"+result.comment);
         return false;
        }
      logger.Write(setup,"emergency_close_submitted",SMCStateName(setup.state),0.0,0.0,
                   (int)result.retcode,reason);
      return true;
     }

   bool ModifyPendingTarget(SMC_SETUP &setup,CSMCLogger &logger,const double new_target,
                            const datetime target_time,const double new_net_rr,
                            const double risk_percent)
     {
      if(setup.state!=SMC_ORDER_PENDING || setup.order_ticket==0 || new_target<=0.0)
         return false;
      if(new_net_rr<SMC_MIN_NET_RR)
         return false;
      if(!OrderSelect(setup.order_ticket))
         return false;
      double tick_size=0.0;
      long digits=0;
      if(!SymbolInfoDouble(m_symbol,SYMBOL_TRADE_TICK_SIZE,tick_size) || tick_size<=0.0 ||
         !SymbolInfoInteger(m_symbol,SYMBOL_DIGITS,digits))
         return false;
      const double normalized=(setup.direction==SMC_DIRECTION_BUY ?
                               SMCFloorToTick(new_target,tick_size) :
                               SMCCeilToTick(new_target,tick_size));
      if((setup.direction==SMC_DIRECTION_BUY && normalized<=setup.entry_price) ||
         (setup.direction==SMC_DIRECTION_SELL && normalized>=setup.entry_price))
         return false;
      MqlTradeRequest request={};
      MqlTradeResult result={};
      request.action=TRADE_ACTION_MODIFY;
      request.order=setup.order_ticket;
      request.price=setup.entry_price;
      request.sl=setup.stop_loss;
      request.tp=NormalizeDouble(normalized,(int)digits);
      request.type_time=ORDER_TIME_SPECIFIED;
      request.expiration=setup.order_expiry;
      if(!OrderSend(request,result) ||
         (result.retcode!=TRADE_RETCODE_DONE && result.retcode!=TRADE_RETCODE_PLACED))
        {
         logger.Write(setup,"order_target_modify_failed","ORDER_PENDING",0.0,risk_percent,
                      (int)result.retcode,"liquidity_target_update_failed_"+result.comment);
         return false;
        }
      setup.take_profit=normalized;
      setup.target_liquidity_time=target_time;
      setup.expected_net_rr=new_net_rr;
      logger.Write(setup,"order_target_modified","ORDER_PENDING",0.0,risk_percent,
                   (int)result.retcode,"next_liquidity_target_revalidated");
      return true;
     }
  };

#endif
