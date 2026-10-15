#property copyright "SMC-H1M5-SweepFVG research fork"
#property link      "https://github.com/cryptyroot-ux/ICT-MT5"
#property version   "1.00"
#property strict

#include <SMC_Config.mqh>
#include <SMC_MarketStructure.mqh>
#include <SMC_Liquidity.mqh>
#include <SMC_Displacement.mqh>
#include <SMC_FVG.mqh>
#include <SMC_SetupStateMachine.mqh>
#include <SMC_RiskManager.mqh>
#include <SMC_TradeLedger.mqh>
#include <SMC_Logger.mqh>
#include <SMC_Execution.mqh>

input group "=== SMC-H1M5-SweepFVG / XAUUSD ==="
input string         InpSMCSymbol                         = "XAUUSD";
input long           InpSMCMagic                          = 15101501;
input double         InpSMCRiskPercent                    = SMC_DEFAULT_RISK_PERCENT;
input double         InpSMCDailyLossPercent               = SMC_DAILY_LOSS_PERCENT;

input group "=== VERIFIED TRANSACTION COSTS ==="
input bool           InpSMCCostModelVerified              = false;
input double         InpSMCRoundTurnCommissionPerLot      = 0.0;
input double         InpSMCRoundTurnSlippagePerLot        = 0.0;

input group "=== LOGGING ==="
input SMC_LOG_LEVEL  InpSMCLogLevel                       = SMC_LOG_INFO;

CSMCMarketStructure  g_smc_market;
CSMCLiquidity        g_smc_liquidity;
CSMCDisplacement     g_smc_displacement;
CSMCFairValueGap     g_smc_fvg;
CSMCSetupStateMachine g_smc_states;
CSMCRiskManager      g_smc_risk;
CSMCTradeLedger      g_smc_ledger;
CSMCLogger           g_smc_logger;
CSMCExecution        g_smc_execution;
SMC_SETUP            g_smc_setup;

bool                 g_smc_enabled=false;
bool                 g_smc_recovery_ready=false;
datetime             g_smc_last_m5_forming_bar=0;
datetime             g_smc_last_consumed_bias=0;
string               g_smc_bias_cursor_key="";

string BiasCursorKey()
  {
   return StringFormat("SMC_BIAS_%I64d_%I64d_%s",
                       (long)AccountInfoInteger(ACCOUNT_LOGIN),InpSMCMagic,InpSMCSymbol);
  }

datetime ServerNow()
  {
   datetime now=TimeTradeServer();
   if(now<=0) now=TimeCurrent();
   return now;
  }

bool SaveConsumedBias(const datetime confirmation_time)
  {
   if(confirmation_time<=0 || g_smc_bias_cursor_key=="")
      return false;
   if(GlobalVariableSet(g_smc_bias_cursor_key,(double)confirmation_time)==0)
      return false;
   GlobalVariablesFlush();
   g_smc_last_consumed_bias=confirmation_time;
   return true;
  }

void LogTransition(const SMC_SETUP_STATE next,const datetime event_time,
                   const string event,const string reason)
  {
   const string before=SMCStateName(g_smc_setup.state);
   if(!g_smc_states.Transition(g_smc_setup,next,event_time,reason))
     {
      g_smc_logger.Write(g_smc_setup,"transition_rejected",before,0.0,
                         InpSMCRiskPercent,0,"illegal_transition_"+event);
      return;
     }
   g_smc_logger.Write(g_smc_setup,event,before,0.0,InpSMCRiskPercent,0,reason);
  }

bool LoadLatestClosedM5(MqlRates &bar)
  {
   MqlRates rates[];
   ArrayResize(rates,2);
   ArraySetAsSeries(rates,true);
   const int copied=CopyRates(InpSMCSymbol,SMC_EXECUTION_TF,1,1,rates);
   if(copied!=1)
      return false;
   bar=rates[0];
   return (bar.time>0);
  }

bool BiasStillValid() 
  {
   if(!g_smc_market.Refresh())
      return false;
   const SMC_STRUCTURE_EVENT current=g_smc_market.LastBOS();
   if(g_smc_market.Bias()!=g_smc_setup.direction || current.confirmation_time<=0)
      return false;
   if(current.direction!=g_smc_setup.direction)
      return false;
   return true;
  }

bool CurrentSetupInvalid(const SMC_FVG &gap,const MqlRates &last_closed,string &reason)
  {
   reason="";
   if(!BiasStillValid())
     {
      reason="H1_bias_neutral_or_opposed";
      return true;
     }
   if(g_smc_setup.direction==SMC_DIRECTION_BUY && last_closed.close<g_smc_setup.sweep_extreme)
     {
      reason="completed_M5_close_below_sweep_extreme";
      return true;
     }
   if(g_smc_setup.direction==SMC_DIRECTION_SELL && last_closed.close>g_smc_setup.sweep_extreme)
     {
      reason="completed_M5_close_above_sweep_extreme";
      return true;
     }
   const SMC_FVG_STATE status=g_smc_fvg.StatusAfterCreation(gap);
   if(status==SMC_FVG_CONSUMED || status==SMC_FVG_INVALIDATED)
     {
      reason=(status==SMC_FVG_CONSUMED ? "FVG_consumed" : "FVG_invalidated");
      return true;
     }
   if(g_smc_risk.DailyLossReached() ||
      g_smc_ledger.ConsecutiveLosses()>=SMC_MAX_CONSECUTIVE_LOSSES)
     {
      reason="daily_loss_or_consecutive_loss_entry_block";
      return true;
     }
   return false;
  }

void InvalidateCurrentSetup(const string reason,const bool expired=false)
  {
   if(g_smc_setup.state==SMC_ORDER_PENDING)
     {
      if(!g_smc_execution.CancelPending(g_smc_setup,g_smc_states,g_smc_logger,
                                        reason,expired,InpSMCRiskPercent))
         return;
      return;
     }
   if(g_smc_setup.state==SMC_IN_POSITION)
     {
      g_smc_execution.CancelResidualOrder(g_smc_setup,g_smc_logger,reason,InpSMCRiskPercent);
      return; // Existing position retains server-side SL/TP; the daily entry gate never closes it.
     }
   LogTransition(expired ? SMC_EXPIRED : SMC_INVALIDATED,ServerNow(),
                 expired ? "setup_expired" : "setup_invalidated",reason);
  }

void EnforcePositionRisk()
  {
   if(g_smc_setup.state!=SMC_IN_POSITION || g_smc_setup.position_id==0)
      return;
   ulong ticket=0;
   double volume=0.0;
   double average_price=0.0;
   if(!g_smc_ledger.OwnedPositionSnapshot(g_smc_setup.position_id,ticket,volume,average_price))
      return;
   g_smc_setup.position_volume=volume;
   if(g_smc_setup.actual_fill_price<=0.0)
     {
      g_smc_setup.actual_fill_price=average_price;
      // Adverse slippage: BUY filling above the limit, SELL filling below it.
      if(g_smc_setup.direction==SMC_DIRECTION_BUY)
         g_smc_setup.actual_slippage=average_price-g_smc_setup.entry_price;
      else
         g_smc_setup.actual_slippage=g_smc_setup.entry_price-average_price;
      g_smc_logger.Write(g_smc_setup,"position_risk_reconciled",SMCStateName(g_smc_setup.state),
                         0.0,InpSMCRiskPercent,0,"actual_open_price_and_volume_read_from_terminal");
     }
   if(g_smc_setup.actual_fill_price<=0.0 || g_smc_setup.stop_loss<=0.0 || volume<=0.0)
      return;
   MqlTick tick;
   if(!SymbolInfoTick(InpSMCSymbol,tick))
      return;
   double actual_loss=0.0;
   if(!g_smc_risk.CalculateActualLoss(g_smc_setup.direction,g_smc_setup.actual_fill_price,
                                      g_smc_setup.stop_loss,volume,tick.ask-tick.bid,actual_loss))
      return;
   g_smc_setup.expected_risk=actual_loss;
   if(actual_loss>g_smc_risk.RiskBudget()+0.01)
      g_smc_execution.EmergencyClose(ticket,g_smc_setup.direction,volume,g_smc_logger,
                                      g_smc_setup,"actual_fill_makes_stop_risk_exceed_budget");
  }

void HandleNewClosedM5()
  {
   const datetime now=ServerNow();
   if(!g_smc_ledger.Recover())
     {
      g_smc_logger.Write(g_smc_setup,"entries_blocked",SMCStateName(g_smc_setup.state),
                         0.0,InpSMCRiskPercent,0,"trade_history_reconciliation_failed");
      return;
     }
   if(!g_smc_risk.RefreshDay())
     {
      g_smc_logger.Write(g_smc_setup,"entries_blocked",SMCStateName(g_smc_setup.state),
                         0.0,InpSMCRiskPercent,0,"daily_equity_reference_unreliable");
      return;
     }

   if(g_smc_setup.state==SMC_COMPLETED || g_smc_setup.state==SMC_INVALIDATED ||
      g_smc_setup.state==SMC_EXPIRED)
      g_smc_states.Reset(g_smc_setup,now);

   if(g_smc_setup.state==SMC_IN_POSITION)
     {
      if(!g_smc_ledger.HasOwnedPosition())
        {
         double pnl=0.0;
         bool completed=false;
         if(g_smc_setup.position_id>0 &&
            g_smc_ledger.PositionLifecycleNetPnl(g_smc_setup.position_id,pnl,completed) && completed)
           {
            const string before=SMCStateName(g_smc_setup.state);
            if(g_smc_states.Transition(g_smc_setup,SMC_COMPLETED,now,"position_lifecycle_reconciled_closed"))
               g_smc_logger.Write(g_smc_setup,"position_completed",before,0.0,
                                  InpSMCRiskPercent,0,"actual_position_lifecycle_pnl",pnl);
           }
        }
      return;
     }

   if(g_smc_setup.state==SMC_WAIT_BIAS)
     {
      if(!g_smc_market.Refresh() || !g_smc_market.HasBias())
         return;
      const SMC_STRUCTURE_EVENT bos=g_smc_market.LastBOS();
      if(bos.confirmation_time<=g_smc_last_consumed_bias)
         return; // A BOS event is consumed once; it cannot seed a second setup after restart.
      g_smc_setup.direction=bos.direction;
      g_smc_setup.h1_bos_level=bos.level;
      g_smc_setup.h1_bos_time=bos.pivot_time;
      g_smc_setup.h1_bos_confirmation=bos.confirmation_time;
      g_smc_setup.setup_start_time=now;
      if(!SaveConsumedBias(bos.confirmation_time))
        {
         g_smc_logger.Write(g_smc_setup,"setup_rejected","WAIT_BIAS",0.0,
                            InpSMCRiskPercent,0,"cannot_persist_consumed_H1_BOS_cursor");
         return;
        }
      LogTransition(SMC_WAIT_SWEEP,bos.confirmation_time,"bias_confirmed",
                    "completed_H1_close_broke_confirmed_swing");
      return;
     }

   if(g_smc_market.Refresh() && g_smc_market.Bias()!=g_smc_setup.direction)
     {
      InvalidateCurrentSetup("H1_bias_changed_before_order");
      return;
     }

   if(g_smc_setup.state==SMC_WAIT_SWEEP)
     {
      SMC_SWEEP_EVENT sweep;
      if(!g_smc_liquidity.DetectSweep(g_smc_setup.direction,g_smc_setup.h1_bos_confirmation,sweep))
         return;
      g_smc_setup.setup_id=sweep.setup_id;
      g_smc_setup.liquidity_level=sweep.level;
      g_smc_setup.sweep_extreme=sweep.extreme;
      g_smc_setup.sweep_time=sweep.excursion_time;
      g_smc_setup.sweep_confirmation=sweep.confirmation_time;
      LogTransition(SMC_WAIT_DISPLACEMENT_CHOCH,sweep.confirmation_time,
                    "sweep_reclaim_confirmed","closed_M5_excursion_and_reclaim");
      return;
     }

   if(g_smc_setup.state==SMC_WAIT_DISPLACEMENT_CHOCH)
     {
      const datetime setup_timeout=g_smc_setup.sweep_confirmation+
                                   12*PeriodSeconds(SMC_EXECUTION_TF);
      MqlRates latest;
      if(!LoadLatestClosedM5(latest))
         return;
      if(latest.time+PeriodSeconds(SMC_EXECUTION_TF)>setup_timeout)
        {
         InvalidateCurrentSetup("no_post_sweep_displacement_within_12_M5_bars",true);
         return;
        }
      SMC_SWEEP_EVENT sweep;
      ZeroMemory(sweep);
      sweep.valid=true;
      sweep.direction=g_smc_setup.direction;
      sweep.level=g_smc_setup.liquidity_level;
      sweep.extreme=g_smc_setup.sweep_extreme;
      sweep.excursion_time=g_smc_setup.sweep_time;
      sweep.confirmation_time=g_smc_setup.sweep_confirmation;
      sweep.setup_id=g_smc_setup.setup_id;
      SMC_DISPLACEMENT_EVENT displacement;
      if(!g_smc_displacement.FindPostSweepEvent(sweep,displacement))
         return;
      g_smc_setup.choch_level=displacement.broken_level;
      g_smc_setup.displacement_bar_time=displacement.displacement_bar_time;
      g_smc_setup.choch_confirmation=displacement.confirmation_time;
      g_smc_setup.atr_value=displacement.atr_value;
      LogTransition(SMC_WAIT_FVG,displacement.confirmation_time,
                    "post_sweep_displacement_choch_confirmed",
                    "post_sweep_confirmed_internal_close_break_and_displacement");
      return;
     }

   if(g_smc_setup.state==SMC_WAIT_FVG)
     {
      SMC_SWEEP_EVENT sweep;
      ZeroMemory(sweep);
      sweep.valid=true;
      sweep.direction=g_smc_setup.direction;
      sweep.confirmation_time=g_smc_setup.sweep_confirmation;
      SMC_DISPLACEMENT_EVENT displacement;
      ZeroMemory(displacement);
      displacement.valid=true;
      displacement.direction=g_smc_setup.direction;
      displacement.displacement_bar_time=g_smc_setup.displacement_bar_time;
      displacement.confirmation_time=g_smc_setup.choch_confirmation;
      SMC_FVG gap;
      if(g_smc_fvg.FindAssociated(sweep,displacement,gap))
        {
         g_smc_setup.fvg_lower=gap.lower;
         g_smc_setup.fvg_upper=gap.upper;
         g_smc_setup.fvg_creation=gap.creation_time;
         g_smc_setup.order_reference_time=gap.creation_time;
         g_smc_setup.atr_value=g_smc_displacement.CurrentCompletedATR();
         if(g_smc_setup.atr_value<=0.0)
           {
            InvalidateCurrentSetup("completed_M5_ATR_unavailable");
            return;
           }
         LogTransition(SMC_WAIT_RETRACE_OR_ORDER,g_smc_setup.order_reference_time,
                       "displacement_fvg_confirmed","three_completed_candles_C2_is_displacement");
         return;
        }
      MqlRates latest;
       if(LoadLatestClosedM5(latest) && latest.time>=g_smc_setup.displacement_bar_time+
                                          PeriodSeconds(SMC_EXECUTION_TF))
         InvalidateCurrentSetup("qualifying_displacement_created_no_three_candle_FVG",true);
      return;
     }

   if(g_smc_setup.state==SMC_WAIT_RETRACE_OR_ORDER)
     {
      if(!g_smc_risk.CanOpenTrade(g_smc_ledger.ConsecutiveLosses()))
        {
         InvalidateCurrentSetup("daily_risk_guard_blocks_new_order");
         return;
        }
      double entry=(g_smc_setup.fvg_lower+g_smc_setup.fvg_upper)*0.5;
      const double tick_size=SymbolInfoDouble(InpSMCSymbol,SYMBOL_TRADE_TICK_SIZE);
      if(tick_size<=0.0)
        {
         InvalidateCurrentSetup("symbol_tick_size_unavailable");
         return;
        }
      entry=SMCRoundToTick(entry,tick_size);
      double target=0.0;
      datetime target_time=0;
      if(!g_smc_liquidity.FindNextLiquidity(g_smc_setup.direction,entry,target,target_time))
        {
         InvalidateCurrentSetup("no_valid_next_opposing_liquidity_target");
         return;
        }
       g_smc_setup.take_profit=target;
      g_smc_setup.target_liquidity_time=target_time;
      if(g_smc_execution.SubmitMidpointLimit(g_smc_setup,g_smc_states,g_smc_risk,
                                             g_smc_ledger,g_smc_logger,InpSMCRiskPercent))
         return;
      if(!g_smc_execution.HasUnresolvedRequest())
         InvalidateCurrentSetup("pending_limit_submission_rejected");
      return;
     }
  }

void ManagePendingSetup(const bool new_m5_bar)
  {
   if(g_smc_setup.state==SMC_ORDER_PENDING)
     {
      datetime now=ServerNow();
      const int bars_after_reference=iBarShift(InpSMCSymbol,SMC_EXECUTION_TF,
                                               g_smc_setup.order_reference_time,false);
      if(g_smc_setup.order_expiry>0 &&
         (now>=g_smc_setup.order_expiry || bars_after_reference>=SMC_PENDING_EXPIRY_BARS))
        {
         InvalidateCurrentSetup("pending_order_expired_at_earlier_of_6_bars_or_30_minutes",true);
         return;
        }
      MqlRates latest;
      if(!LoadLatestClosedM5(latest))
         return;
      SMC_FVG gap;
      ZeroMemory(gap);
      gap.valid=true;
      gap.direction=g_smc_setup.direction;
      gap.lower=g_smc_setup.fvg_lower;
      gap.upper=g_smc_setup.fvg_upper;
      gap.creation_time=g_smc_setup.fvg_creation;
      string invalid_reason="";
      if(CurrentSetupInvalid(gap,latest,invalid_reason))
        {
         InvalidateCurrentSetup(invalid_reason);
         return;
        }
      if(!new_m5_bar)
         return;

      double updated_target=0.0;
      datetime target_time=0;
      if(!g_smc_liquidity.FindNextLiquidity(g_smc_setup.direction,g_smc_setup.entry_price,
                                            updated_target,target_time))
        {
         InvalidateCurrentSetup("next_liquidity_target_disappeared");
         return;
        }
      MqlTick tick;
      if(!SymbolInfoTick(InpSMCSymbol,tick))
         return;
      double updated_rr=0.0;
      if(!g_smc_risk.CalculateNetRewardRisk(g_smc_setup.direction,g_smc_setup.entry_price,
                                           g_smc_setup.stop_loss,updated_target,g_smc_setup.volume,
                                           tick.ask-tick.bid,updated_rr) || updated_rr<SMC_MIN_NET_RR)
        {
         InvalidateCurrentSetup("changed_liquidity_target_fails_net_1_to_3_gate");
         return;
        }
      const double tick_size=SymbolInfoDouble(InpSMCSymbol,SYMBOL_TRADE_TICK_SIZE);
      if(tick_size>0.0 && MathAbs(updated_target-g_smc_setup.take_profit)>=tick_size)
        {
         if(!g_smc_execution.ModifyPendingTarget(g_smc_setup,g_smc_logger,updated_target,
                                                 target_time,updated_rr,InpSMCRiskPercent))
            InvalidateCurrentSetup("liquidity_target_changed_but_pending_order_modify_failed");
        }
     }
   else if(g_smc_setup.state==SMC_IN_POSITION)
     {
      EnforcePositionRisk();
      MqlRates latest;
      if(LoadLatestClosedM5(latest))
        {
         SMC_FVG gap;
         ZeroMemory(gap);
         gap.valid=true;
         gap.direction=g_smc_setup.direction;
         gap.lower=g_smc_setup.fvg_lower;
         gap.upper=g_smc_setup.fvg_upper;
         gap.creation_time=g_smc_setup.fvg_creation;
         string invalid_reason="";
         if(CurrentSetupInvalid(gap,latest,invalid_reason))
            g_smc_execution.CancelResidualOrder(g_smc_setup,g_smc_logger,
                                                invalid_reason,InpSMCRiskPercent);
        }
      if(g_smc_risk.DailyLossReached())
         g_smc_execution.CancelResidualOrder(g_smc_setup,g_smc_logger,
                                             "daily_loss_cap_cancelled_unfilled_remainder",InpSMCRiskPercent);
      if(!g_smc_ledger.HasOwnedPosition())
        {
         double pnl=0.0;
         bool completed=false;
         if(g_smc_setup.position_id>0 &&
            g_smc_ledger.PositionLifecycleNetPnl(g_smc_setup.position_id,pnl,completed) && completed)
           {
            const string before=SMCStateName(g_smc_setup.state);
            if(g_smc_states.Transition(g_smc_setup,SMC_COMPLETED,ServerNow(),"position_lifecycle_reconciled_closed"))
               g_smc_logger.Write(g_smc_setup,"position_completed",before,0.0,
                                  InpSMCRiskPercent,0,"actual_position_lifecycle_pnl",pnl);
           }
        }
     }
  }

void ProcessSMC()
  {
   if(!g_smc_enabled)
      return;
   if(!g_smc_recovery_ready)
     {
      g_smc_recovery_ready=g_smc_ledger.Recover();
      if(!g_smc_recovery_ready)
         return;
     }
   if(!g_smc_market.Refresh())
      return;
   if(!g_smc_risk.RefreshDay())
      return;

   datetime forming_time=iTime(InpSMCSymbol,SMC_EXECUTION_TF,0);
   const bool new_m5_bar=(forming_time>0 && forming_time!=g_smc_last_m5_forming_bar);
   if(new_m5_bar)
      g_smc_last_m5_forming_bar=forming_time;

   g_smc_execution.Reconcile(g_smc_setup,g_smc_states,g_smc_ledger,g_smc_logger,InpSMCRiskPercent);
   ManagePendingSetup(new_m5_bar);
   if(!new_m5_bar || g_smc_setup.state==SMC_ORDER_PENDING ||
      g_smc_setup.state==SMC_IN_POSITION || g_smc_execution.HasUnresolvedRequest())
      return;
   if(g_smc_risk.DailyLossReached() ||
      g_smc_ledger.ConsecutiveLosses()>=SMC_MAX_CONSECUTIVE_LOSSES)
     {
      if(g_smc_setup.state!=SMC_WAIT_BIAS)
         InvalidateCurrentSetup("daily_loss_or_two_consecutive_loss_guard");
      return;
     }
   HandleNewClosedM5();
  }

int OnInit()
  {
   if(!g_smc_logger.Init(InpSMCSymbol,InpSMCMagic,InpSMCRiskPercent,
                         SMC_MAX_SPREAD_PRICE,InpSMCRoundTurnCommissionPerLot,
                         InpSMCRoundTurnSlippagePerLot,InpSMCCostModelVerified,InpSMCLogLevel))
     {
      Print("[SMC] init failed: event CSV cannot be opened");
      return INIT_FAILED;
     }
   string config_reason="";
   if(!SMCValidateConfiguration(InpSMCSymbol,SMC_EXECUTION_TF,InpSMCRiskPercent,
                                InpSMCDailyLossPercent,InpSMCCostModelVerified,
                                InpSMCRoundTurnCommissionPerLot,
                                InpSMCRoundTurnSlippagePerLot,config_reason))
     {
      g_smc_states.Reset(g_smc_setup,ServerNow());
      g_smc_logger.Write(g_smc_setup,"configuration_rejected","INIT",0.0,
                         InpSMCRiskPercent,0,config_reason);
      return INIT_PARAMETERS_INCORRECT;
     }
   if(AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_REAL)
     {
      g_smc_states.Reset(g_smc_setup,ServerNow());
      g_smc_logger.Write(g_smc_setup,"configuration_rejected","INIT",0.0,
                         InpSMCRiskPercent,0,"real_accounts_are_disabled_research_demo_only");
      return INIT_FAILED;
     }
   if(!g_smc_market.Init(InpSMCSymbol) || !g_smc_liquidity.Init(InpSMCSymbol) ||
      !g_smc_displacement.Init(InpSMCSymbol) || !g_smc_fvg.Init(InpSMCSymbol) ||
      !g_smc_risk.Init(InpSMCSymbol,InpSMCMagic,InpSMCRiskPercent,InpSMCDailyLossPercent,
                       InpSMCRoundTurnCommissionPerLot,InpSMCRoundTurnSlippagePerLot,
                       InpSMCCostModelVerified) || !g_smc_ledger.Init(InpSMCSymbol,InpSMCMagic) ||
      !g_smc_execution.Init(InpSMCSymbol,InpSMCMagic))
     {
      g_smc_logger.Write(g_smc_setup,"initialization_failed","INIT",0.0,
                         InpSMCRiskPercent,0,"module_or_equity_reference_initialization_failed");
      return INIT_FAILED;
     }

   if(!g_smc_ledger.Recover())
     {
      g_smc_logger.Write(g_smc_setup,"recovery_blocked","INIT",0.0,
                         InpSMCRiskPercent,0,"deal_history_could_not_reconstruct_loss_streak");
      g_smc_recovery_ready=false;
     }
   else
      g_smc_recovery_ready=true;

   const datetime now=ServerNow();
   g_smc_states.Reset(g_smc_setup,now);
   g_smc_bias_cursor_key=BiasCursorKey();
   if(GlobalVariableCheck(g_smc_bias_cursor_key))
      g_smc_last_consumed_bias=(datetime)GlobalVariableGet(g_smc_bias_cursor_key);
   else
      g_smc_last_consumed_bias=0;

   g_smc_execution.RecoverAtStartup(g_smc_setup,g_smc_states,g_smc_ledger,g_smc_logger);
   g_smc_last_m5_forming_bar=iTime(InpSMCSymbol,SMC_EXECUTION_TF,0);
   if(!EventSetTimer(1))
     {
       g_smc_logger.Write(g_smc_setup,"initialization_failed","INIT",0.0,
                         InpSMCRiskPercent,0,"one_second_reconciliation_timer_failed");
      return INIT_FAILED;
     }
   g_smc_enabled=true;
   g_smc_logger.Write(g_smc_setup,"initialized","INIT",0.0,InpSMCRiskPercent,0,
                      "research_demo_only_real_account_blocked");
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   if(g_smc_enabled)
      g_smc_logger.Write(g_smc_setup,"deinitialized",SMCStateName(g_smc_setup.state),
                         0.0,InpSMCRiskPercent,reason,
                         "expert_removed_or_terminal_stopped");
  }

void OnTick()
  {
   ProcessSMC();
  }

void OnTimer()
  {
   ProcessSMC();
  }

void OnTradeTransaction(const MqlTradeTransaction &trans,const MqlTradeRequest &request,
                        const MqlTradeResult &result)
  {
   if(trans.type!=TRADE_TRANSACTION_DEAL_ADD || trans.deal==0)
      return;
   if(!HistoryDealSelect(trans.deal))
      return;
   if(HistoryDealGetString(trans.deal,DEAL_SYMBOL)!=InpSMCSymbol ||
      HistoryDealGetInteger(trans.deal,DEAL_MAGIC)!=InpSMCMagic)
      return;

   const ulong position_id=(ulong)HistoryDealGetInteger(trans.deal,DEAL_POSITION_ID);
   const long entry_type=HistoryDealGetInteger(trans.deal,DEAL_ENTRY);
   const double profit=HistoryDealGetDouble(trans.deal,DEAL_PROFIT);
   const double commission=HistoryDealGetDouble(trans.deal,DEAL_COMMISSION);
   const double swap=HistoryDealGetDouble(trans.deal,DEAL_SWAP);
   const double fee=HistoryDealGetDouble(trans.deal,DEAL_FEE);
   if(!g_smc_ledger.OnDealAdded(trans.deal))
      g_smc_recovery_ready=false;

   if((entry_type==DEAL_ENTRY_IN || entry_type==DEAL_ENTRY_INOUT) &&
      (g_smc_setup.state==SMC_ORDER_PENDING || g_smc_setup.state==SMC_WAIT_RETRACE_OR_ORDER))
     {
      const double deal_price=HistoryDealGetDouble(trans.deal,DEAL_PRICE);
      const double deal_volume=HistoryDealGetDouble(trans.deal,DEAL_VOLUME);
      g_smc_setup.position_id=position_id;
      g_smc_setup.deal_id=trans.deal;
      g_smc_setup.actual_fill_price=deal_price;
      if(g_smc_setup.entry_price>0.0 && deal_price>0.0)
        {
         if(g_smc_setup.direction==SMC_DIRECTION_BUY)
            g_smc_setup.actual_slippage=deal_price-g_smc_setup.entry_price;
         else
            g_smc_setup.actual_slippage=g_smc_setup.entry_price-deal_price;
        }
      if(deal_volume>0.0)
        {
         g_smc_setup.position_volume+=deal_volume;
         g_smc_setup.volume=MathMax(g_smc_setup.volume,deal_volume);
        }
      if(g_smc_setup.state==SMC_WAIT_RETRACE_OR_ORDER)
         g_smc_states.Transition(g_smc_setup,SMC_ORDER_PENDING,ServerNow(),"accepted_limit_request_reconciled");
      const string before=SMCStateName(g_smc_setup.state);
      if(g_smc_states.Transition(g_smc_setup,SMC_IN_POSITION,ServerNow(),"deal_fill_confirmed"))
         g_smc_logger.Write(g_smc_setup,"position_fill",before,0.0,InpSMCRiskPercent,
                            (int)result.retcode,"actual_deal_entry",profit,commission,swap,fee);
     }
   else
     {
      double lifecycle_pnl=0.0;
      bool completed=false;
      if(position_id>0 && g_smc_ledger.PositionLifecycleNetPnl(position_id,lifecycle_pnl,completed) &&
         completed && g_smc_setup.state==SMC_IN_POSITION && g_smc_setup.position_id==position_id)
        {
         g_smc_execution.CancelResidualOrder(g_smc_setup,g_smc_logger,
                                             "position_lifecycle_closed",InpSMCRiskPercent);
         const string before=SMCStateName(g_smc_setup.state);
         if(g_smc_states.Transition(g_smc_setup,SMC_COMPLETED,ServerNow(),"position_lifecycle_closed"))
            g_smc_logger.Write(g_smc_setup,"position_completed",before,0.0,InpSMCRiskPercent,
                               (int)result.retcode,"actual_position_lifecycle_pnl",
                               lifecycle_pnl,commission,swap,fee);
        }
      else
          g_smc_logger.Write(g_smc_setup,"deal_recorded",SMCStateName(g_smc_setup.state),
                             0.0,InpSMCRiskPercent,
                            (int)result.retcode,"actual_deal_costs_lifecycle_not_yet_complete",
                            profit,commission,swap,fee);
     }
  }
