#ifndef SMC_TRADE_LEDGER_MQH
#define SMC_TRADE_LEDGER_MQH

#include <SMC_Config.mqh>

class CSMCTradeLedger
  {
private:
   string m_symbol;
   long   m_magic;
   int    m_consecutive_losses;
   bool   m_reliable;

   bool IsPositionIdentifierOpen(const ulong identifier) const
     {
      for(int index=0;index<PositionsTotal();index++)
        {
         const ulong ticket=PositionGetTicket(index);
         if(ticket==0)
            continue;
         if((ulong)PositionGetInteger(POSITION_IDENTIFIER)==identifier)
            return true;
        }
      return false;
     }

   bool AlreadySeen(const ulong &identifiers[],const ulong identifier) const
     {
      for(int index=0;index<ArraySize(identifiers);index++)
         if(identifiers[index]==identifier)
            return true;
      return false;
     }

   bool LifecycleResult(const ulong identifier,double &net_pnl,double &commission_total,
                        double &swap_total,double &fee_total,bool &completed) const
     {
      net_pnl=0.0;
      commission_total=0.0;
      swap_total=0.0;
      fee_total=0.0;
      completed=false;
      if(!HistorySelectByPosition(identifier))
         return false;
      double entry_volume=0.0,exit_volume=0.0;
      bool has_exit=false;
      const int total=HistoryDealsTotal();
      for(int index=0;index<total;index++)
        {
         const ulong deal=HistoryDealGetTicket(index);
         if(deal==0 || HistoryDealGetString(deal,DEAL_SYMBOL)!=m_symbol ||
            HistoryDealGetInteger(deal,DEAL_MAGIC)!=m_magic)
            continue;
         const long entry=HistoryDealGetInteger(deal,DEAL_ENTRY);
         const double volume=HistoryDealGetDouble(deal,DEAL_VOLUME);
         if(entry==DEAL_ENTRY_IN)
            entry_volume+=volume;
         else if(entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY)
           {
            exit_volume+=volume;
            has_exit=true;
           }
         else if(entry==DEAL_ENTRY_INOUT)
            return false; // Reversal deals need two position lifecycles; fail closed.

          net_pnl+=HistoryDealGetDouble(deal,DEAL_PROFIT);
          commission_total+=HistoryDealGetDouble(deal,DEAL_COMMISSION);
          swap_total+=HistoryDealGetDouble(deal,DEAL_SWAP);
          fee_total+=HistoryDealGetDouble(deal,DEAL_FEE);
         }
      net_pnl+=commission_total+swap_total+fee_total;
      completed=(has_exit && exit_volume+1e-8>=entry_volume && !IsPositionIdentifierOpen(identifier));
      return true;
     }

   bool RebuildLossStreak()
     {
      m_consecutive_losses=0;
      m_reliable=false;
      datetime now=TimeTradeServer();
      if(now<=0) now=TimeCurrent();
      MqlDateTime server_date;
      TimeToStruct(now,server_date);
      server_date.hour=0;
      server_date.min=0;
      server_date.sec=0;
      const datetime day_start=StructToTime(server_date);
      if(day_start<=0 || !HistorySelect(day_start,now))
         return false;
      const int total=HistoryDealsTotal();
      ulong identifiers[];
      ArrayResize(identifiers,0);

      // Copy candidate identifiers first because HistorySelectByPosition changes the terminal selection.
      for(int index=total-1;index>=0;index--)
        {
         const ulong deal=HistoryDealGetTicket(index);
         if(deal==0 || HistoryDealGetString(deal,DEAL_SYMBOL)!=m_symbol ||
            HistoryDealGetInteger(deal,DEAL_MAGIC)!=m_magic)
            continue;
         const long entry=HistoryDealGetInteger(deal,DEAL_ENTRY);
         if(entry!=DEAL_ENTRY_OUT && entry!=DEAL_ENTRY_OUT_BY && entry!=DEAL_ENTRY_INOUT)
            continue;
         const ulong identifier=(ulong)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
         if(identifier==0 || AlreadySeen(identifiers,identifier) || IsPositionIdentifierOpen(identifier))
            continue;
         const int size=ArraySize(identifiers);
         if(ArrayResize(identifiers,size+1)!=size+1)
            return false;
         identifiers[size]=identifier;
        }

      for(int index=0;index<ArraySize(identifiers);index++)
        {
         double pnl=0.0;
         double commission=0.0,swap=0.0,fee=0.0;
         bool completed=false;
         if(!LifecycleResult(identifiers[index],pnl,commission,swap,fee,completed))
            return false;
         if(!completed)
            return false;
         if(pnl<0.0)
            m_consecutive_losses++;
         else
           {
            // A zero or positive completed lifecycle resets the consecutive-loss sequence.
            m_consecutive_losses=0;
            break;
           }
         if(m_consecutive_losses>=SMC_MAX_CONSECUTIVE_LOSSES)
            break;
        }
      m_reliable=true;
      return true;
     }

public:
                     CSMCTradeLedger()
     {
      m_symbol="";
      m_magic=0;
      m_consecutive_losses=0;
      m_reliable=false;
     }

   bool Init(const string symbol,const long magic)
     {
      m_symbol=symbol;
      m_magic=magic;
      m_consecutive_losses=0;
      m_reliable=false;
      return (symbol!="" && magic>0);
     }

   bool Recover() { return RebuildLossStreak(); }
   bool IsReliable() const { return m_reliable; }
   int ConsecutiveLosses() const { return m_consecutive_losses; }

   bool HasOwnedPosition() const
     {
      for(int index=0;index<PositionsTotal();index++)
        {
         const ulong ticket=PositionGetTicket(index);
         if(ticket==0)
            continue;
         if(PositionGetString(POSITION_SYMBOL)==m_symbol && PositionGetInteger(POSITION_MAGIC)==m_magic)
            return true;
        }
      return false;
     }

   bool OwnedPositionSnapshot(const ulong identifier,ulong &ticket,double &volume,
                              double &average_price) const
     {
      ticket=0;
      volume=0.0;
      average_price=0.0;
      for(int index=0;index<PositionsTotal();index++)
        {
         const ulong current=PositionGetTicket(index);
         if(current==0)
            continue;
         if((ulong)PositionGetInteger(POSITION_IDENTIFIER)==identifier &&
            PositionGetString(POSITION_SYMBOL)==m_symbol &&
            PositionGetInteger(POSITION_MAGIC)==m_magic)
           {
            ticket=current;
            volume=PositionGetDouble(POSITION_VOLUME);
            average_price=PositionGetDouble(POSITION_PRICE_OPEN);
            return (volume>0.0 && average_price>0.0);
           }
        }
      return false;
     }

   bool HasOwnedPendingOrder() const
     {
      for(int index=0;index<OrdersTotal();index++)
        {
         const ulong ticket=OrderGetTicket(index);
         if(ticket==0)
            continue;
         if(OrderGetString(ORDER_SYMBOL)==m_symbol && OrderGetInteger(ORDER_MAGIC)==m_magic)
            return true;
        }
      return false;
     }

   bool OnDealAdded(const ulong deal_ticket)
     {
      if(deal_ticket==0 || !HistoryDealSelect(deal_ticket))
         return false;
      if(HistoryDealGetString(deal_ticket,DEAL_SYMBOL)!=m_symbol ||
         HistoryDealGetInteger(deal_ticket,DEAL_MAGIC)!=m_magic)
         return true;
      return RebuildLossStreak();
     }

   bool PositionLifecycleNetPnl(const ulong identifier,double &net_pnl,bool &completed) const
     {
      double commission=0.0,swap=0.0,fee=0.0;
      return LifecycleResult(identifier,net_pnl,commission,swap,fee,completed);
     }

   bool PositionLifecycleDetails(const ulong identifier,double &net_pnl,double &commission,
                                 double &swap,double &fee,bool &completed) const
     {
      return LifecycleResult(identifier,net_pnl,commission,swap,fee,completed);
     }
  };

#endif
