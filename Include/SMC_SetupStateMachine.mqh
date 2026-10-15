#ifndef SMC_SETUP_STATE_MACHINE_MQH
#define SMC_SETUP_STATE_MACHINE_MQH

#include <SMC_Config.mqh>

class CSMCSetupStateMachine
  {
private:
   bool IsTerminal(const SMC_SETUP_STATE state) const
     {
      return (state==SMC_COMPLETED || state==SMC_INVALIDATED || state==SMC_EXPIRED);
     }

   bool Allowed(const SMC_SETUP_STATE current,const SMC_SETUP_STATE next) const
     {
      if(next==SMC_INVALIDATED)
         return (current==SMC_WAIT_SWEEP || current==SMC_WAIT_DISPLACEMENT_CHOCH ||
                 current==SMC_WAIT_FVG || current==SMC_WAIT_RETRACE_OR_ORDER ||
                 current==SMC_ORDER_PENDING);
      if(next==SMC_EXPIRED)
         return (current==SMC_WAIT_SWEEP || current==SMC_WAIT_DISPLACEMENT_CHOCH ||
                 current==SMC_WAIT_FVG || current==SMC_WAIT_RETRACE_OR_ORDER ||
                 current==SMC_ORDER_PENDING);
      if(current==SMC_WAIT_BIAS && next==SMC_WAIT_SWEEP)
         return true;
      if(current==SMC_WAIT_SWEEP && next==SMC_WAIT_DISPLACEMENT_CHOCH)
         return true;
      if(current==SMC_WAIT_DISPLACEMENT_CHOCH && next==SMC_WAIT_FVG)
         return true;
      if(current==SMC_WAIT_FVG && next==SMC_WAIT_RETRACE_OR_ORDER)
         return true;
      if(current==SMC_WAIT_RETRACE_OR_ORDER && next==SMC_ORDER_PENDING)
         return true;
      if(current==SMC_ORDER_PENDING && next==SMC_IN_POSITION)
         return true;
      if(current==SMC_IN_POSITION && next==SMC_COMPLETED)
         return true;
      return false;
     }

public:
   void Reset(SMC_SETUP &setup,const datetime now)
     {
      ZeroMemory(setup);
      setup.setup_id="";
      setup.state=SMC_WAIT_BIAS;
      setup.direction=SMC_DIRECTION_NONE;
      setup.setup_start_time=now;
      setup.last_transition_time=now;
      setup.last_reason="setup_created";
     }

   bool Transition(SMC_SETUP &setup,const SMC_SETUP_STATE next,
                   const datetime event_time,const string reason) const
     {
      if(IsTerminal(setup.state) || !Allowed(setup.state,next) || event_time<=0 || reason=="")
         return false;
      if(next==SMC_WAIT_SWEEP && (setup.direction==SMC_DIRECTION_NONE || setup.h1_bos_confirmation<=0))
         return false;
      if(next!=SMC_INVALIDATED && next!=SMC_EXPIRED &&
         next>=SMC_WAIT_DISPLACEMENT_CHOCH && setup.setup_id=="")
         return false;
      setup.state=next;
      setup.last_transition_time=event_time;
      setup.last_reason=reason;
      return true;
     }

   bool RecoverPosition(SMC_SETUP &setup,const ulong position_id,const datetime now) const
     {
      if(position_id==0 || now<=0)
         return false;
      ZeroMemory(setup);
      setup.setup_id=StringFormat("RECOVERED-POSITION-%I64d",(long)position_id);
      setup.state=SMC_IN_POSITION;
      setup.position_id=position_id;
      setup.last_transition_time=now;
      setup.last_reason="owned_position_reconciled_after_restart";
      return true;
     }

   bool IsTerminalState(const SMC_SETUP_STATE state) const { return IsTerminal(state); }
  };

#endif
