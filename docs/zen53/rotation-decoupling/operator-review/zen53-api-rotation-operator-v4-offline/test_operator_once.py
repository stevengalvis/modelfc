"""Synthetic orchestration tests. They never invoke systemd or a VPS source."""
import ast
import json
import hashlib
import os
import pathlib
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace

import operator_once as op

import base64
import zlib

# Exact historical source fixtures are bundled in this test file so the
# seven-file deployment suite never reads the author's cloud workspace.
_ADMISSION_B85 = (
    'c%03BYjfgAvgr5z6}sw(3-ka6{Ak-d-dNhEyV<tE2k`XFdK?NN3R}}4v6Ap(X8XUN%&dp1^Z-'
    '1&_nza3nMP7oR#sM4zN#wAw(aXEj91J{{6}W7M}Niwk2S(z@nX5Nr60$B7+7vFv!>xXn6a7VJ@|_md~;U`i{j>+H-'
    'pe}p*j=`J#QVw7X0#;t1wEM*5tvDt>rpStY{725vm`=(1_()F<ZHjn=ou?wTi+=Hmko0!k{r@3w945pso8TT(26B9V_<26+?Io0V'
    'M8X0iWq*CC290?!tO>7i$*REr2y!6Gm^KWWosjMd*FAuy=`{_zdbq%vv%xUPl0kx!!}7_)GQ|%bmthM!>SBRLz6FLb>J}+qU2Eu3'
    '2+0NEY(p!Hpjl{!~8x7DLnGcNoiGD>u;#t)6D`XT;K9ak!2=^&A5!yd;bikl4QuT-'
    '7diPyJp`q2nHlRaxxNn>Uxk^DkrT%&J!Hy+=3N0~Gd_A<%g40i{=q@MVwx)5N~ob*s}lIH_BQ2m1&6M|G>+YPIG6Tj9~cL9PDAl7'
    '9stfpGrJ1(f$-#N$2j2xE}}zkhgmazKDv^8Wz7)vDFrT=xbS{lO>1xfJD?MUOr(>WT$3Ke%rq$!!fY%POs!usCT%?4K~cNnt%eT-'
    '9;Cf3E%5#}4cnbpXv#?_>Yx9?^Dc?}e)*+Y<CP^C6{<*(jD<$0oY*+E18(QTVMf^WA$O*&ZPCbvXLc8xbxxZyhSyt1wQMEV^g-'
    'pmhZwp%l;#e-SYJp|Hb;2beYCd=9YBhr{dMs5|NZ)N_!Voa^Cb|NIwdtJ3@V8h-'
    'XC&SZ2mp7bu9ac}ff|Gei6zYcmMXVkmC?4I|odV`5G>W{xv-n_*r95X9+mn)pYGe05*f62~G7iJv{?F{Fy7Y0cLle}&PVFF+7BNw'
    'J<I0tC{a=A|2slV`(7ZMMCWQ9)wNG7Zg^C@tbAlINi0B%~{;KiCn{ygC`J5FU>)97Y*aNZkFhD6XmXrL-'
    '3>{L0ms&dA3I>+tojCl_%T`z%P&J_((`{@1tVRmwGNZ$=S&xeD_Xn1+qGcYY+MgEVobp4>Y0;Co5RXRu0_pUcR_S}<`&e1XJyl=O'
    '>`Jv|>pUjUt^Zk><*75tP+nybrxX0ef@vJ@fyrbzMJ33<TPu_c#dO@pc&%`h0tfFkg9d^)$PVIXq2Xof(_B}S;Z@B>1Jvw=R!jAU'
    '$PmVnIy?fAZ&rbFak5AnF`Rw@MpfhDBozDAR8wMs`_yKd`_#uOV=e=*S!~KKfBagLP2W@ZOp0zti?_2KC4EjEE58G4kJ!8l3JI6<'
    'd)2VlGI6a)bKR(&tKWyz!_bWIY<8v4e=WF-'
    '!(z(Gw*gtAHFk<+h_y&x0ExxHQ*Q4RZ&H1E195`3q(Wm}^7dT1lUiGeqqhH|b&sg#Jz<LLq(2@VV=?`IcbuZ-iPX6=PQGe1C-'
    '}O%%sq5}!(i;un8z?p}T(0~Dt48)-'
    '{1PW@*^Cz;UH5)x!Qp;`=2Jr`!NzT?apE@ScYo}6zW>@J75wWzKuYumKOqa+0Qj?gYH^trPyOJOe$fy92>(L?bJ-ny!h+|`o5{z<'
    'vHktcn>RByhuIDw!7u=W#Npa7b{-'
    'i_TA$6TH9mZxh(8E>BnTVxaIpxVz%pT=ti!pLfR(^)0&Z^zbAiMN6lIYm;5JdABRruorTs(i=3zuSyKb>(G>`y-A6O-'
    '`uL&Y_8B6r=4~!cSJ0P-90G)vy&_rO(cm<-'
    'Wia%?rXetEH6belTs1P{tsmdXx#gpje)KDI8tx*^zG)}YP{?1`^!4Pv_z<@~5K#SPIMf;$c0W^u23nDhK)`9!z`U`iuVAjwX457z'
    'YJqvNNTo9XFenKOwh{5!_e@IM7^BM0mR;VHg>i7gadodhb{$l-$KMjV152#Vn=Z`?n;aBTl1~B7K6y*4bdgqtJ-'
    'p{@BI#SY^&(0J<YX+Pg;qY(DV4%ea&C#H$&S$k;sel3V;n(Ka>5oUfPgP#887B_Po*K82)w{J11UM`1MOx~fN?EN;<pAW>H~i4X5'
    '5+(vE?2{gUJe(886F-UmRC<}#QyKh`ti)_9Cg|UV3Ae27gzo97$sB6-'
    '_BKU(yipl0s__r(oJ6y=0=hGQq3260)K&73t$5*TuYnE4+Z{t4{Pi@js~#e3<jfaE&ZT+ba;s7C{;adC~&oWgZSsq$y6X<#qAIgp'
    'oMRRUlDebJttrxRS8JG8Fvo;&sMep&KEO*%J7+x<<h#vC72VxEOF@Lvybi~W_fVJMnXBUaF^2=I8k6UAZ%yG9_tqU@`F(P0wxr{f'
    'YIvZQ_A>@B^!yV$RFY-'
    '@#X8_pp0>XSOD<EW6AtVnFJU(!=dEE<(a6Q!zHc6<RF;p9SxrOiiWCI4o~=a&G^CX93F8znh)%G=HG);P$gheQIycAcxqku2bq~Q'
    '_XAX2dBz1c@)lvt3|=cNtx9G@VZ=a*2O#FxOsHF;Jh{7d&=?>+I3O{D=tBxRa(yt~f5Ml&C<>!0)liCixdif6-'
    'RM3RP|za<W=(z5<~3;qECPBpcsv8M0hl8JC+<uub<z82aC3RNnb)9JxK8wH*Zu1rL1R&rd%qDD5ep_|e1@b!7;Xd%&tOh6@(<`er'
    'y&W17>yl--{feV>OzoW5cCBQF}{2!2-2-b(nt@#dBNuHdXbF93n5Akokb>zzrq-Zz#=#5F-siM&EOSRv1}7{e-'
    'I*41yV&e55o4|2g@&_I&}@L-cRJcfvu?-'
    'EFhWDrUv}Uh**ZeD`DV!Fb#gNj#W7>`&AqFfZNht=~fc{5eMer{HR(1TQQwD|8>&?s~DC4@MdCX<_(bN8v}9bAc0h~{6`hFNUI4>'
    '+0uQkf=4C6H?mE`Uw-O=5te9b37R3ohST3P0+W&|5Zin%A&6pSq)=T4Y8<o{@(>veWHBh<TOc!F%K76~19MkcK}lX-'
    '69*8V5ZRh;7v#nO=zpCX0uv3XLJ62E_tyG47=G>cCulrkAO}oSG}#wGSWv0g{foWpPyGupK@)#LUnmS{`LXrQC(G6kG+;vhHLVeI'
    'SHSh~B8n61DO@kWJY~@mEM9^au{8)UVecWN)qOL=syNC2A2FvN%tOvVPp+T%vnu?$2H}4pgvDyBiiQebdq0CycfsvmpyFrnq0|GC'
    'G}GpsHw%<Q1{{Pz0oPCuu~;+q9hB#&$pGT{<%J`fL**+=pdGW0$;2$dH%E~1q&vA87jR1603%Ar3&vJ3S6l70icnlAO5>{FZB6{%'
    ')tWPg$X9JQ_IzJ7meY+m><&ZK1!IBqDEF#>1e{5#YJ5cawK?v8`qIC=OtE9n9$NvMaeZ?chTqmJQjjLdl^e&|I>Rv)M6U|onB@&@'
    ';cGdvgd#E)@hYmt&K3(n$sBGc)bijsWjB5Y+;ZQ6=`_4cSubZwRI+Sye%b2|Zmyl1!Fg{q>7tor&7*Lsfd|XOTL4k5&taU5NPegQ'
    '0Y37D3?9>%?IG5Cd`&?vVEhF>1|VV*-?K0<90Q}R7On>>Hc`<5Vc4Ly2Ilv@B}*Pca18isFn_Sn-'
    '}G<v25llz7Cefu@IextadE0Cm?uAZAir7^v!f}-'
    'eAn|ii$I#4wQ9&3aRQK*SqeT8O8A9qC29f$xHfN;c!$y?&#8<dwJ47CVR$pRuygYbYUL)@hVCwWH02^5-'
    '#E<zgBfKM*a(R|L(#Y#5=B#tQk%e0lou@`6C8~2ke0dvx$r}Cg$HfC5HwKy7pNcjpmczl!K94ASy*E%h(~2&Ywr3B#VIs5K-'
    'r;nDAs$L*XxK~hq3>B4KFwXO)6uK5wC`9M!#-bcPL{kdllvS(EFP8SF4>7yxPp^ei-'
    '*)x?p(kv*uM8;TV8f7>&8;k9y~m;pmsL@Jl8}W5ixIgr|5Q4a_Gn%6Q+}r;YYqUK16@XE=eL6UXQS!ufNZcvY{IuU_Qb3jzKS42M'
    'COe8hNUfy|eGy607ZLf)@a<imao3gk^oWmW7Ny8;3UZLdeJ<eRk?kt(JiN^`xw^?$8a*y?oZ#cVhN?;+#f77laT5$Xr$6W|hU1a='
    'CO>^>-'
    'E1487&UMiAZ4!)QqE9`6cvfmwNx+>ZrJa=^1=bIJEle_p<E)>(RZ&Zy3&<s%+E~}vN=j7SmGre;76mvgx&Xq`o>&YcsVxK`&w2$_'
    'W4{AJejV5obDSNR_eg>liE*5?j5$?(<)~wOwCsz?BKQ#sB0er(pe61-'
    'Q(q<eUdWzM`6<c;?3>tt{Du6>0`QSf_Hp;XdFmQ*s8lFIoHvJe>n7?!v)tY+U=Hm6+hf1E+{UE9GsvvF8tTs4xa(M--hlJlE1QX)'
    '%zFI>`{iD@x9+pex&!AyP!{L?29Wy`vmh$`KJg)MAF}sANnsaALv3S5iJkKMFiTxG($q{?^Zoj<wi~jga5f1rakOM;xE>rSSjmVg'
    ')gA<0<0(g-!qyr}5YMs!!5teAmsL!p<%`7ij(4z7~_+-oKcB2u5jTQ7SvbQ)n@2YlVWrGa^O-hFznT0U86G1G_vbP$$*=Kf+-'
    '8iNO-*Oi?<rV-$?1B%}0uAsV@E%7Mfx%D7y5F&644g?N({5lDn9vfU;J-}enqhKXk8l-f0<O{d=iXJ<HiC0Bm8gy!6%ZJ-LB~<cx'
    'B=4A@n^VbCq@blNk4F=>lwQ7$_&27oi%E`T|mMZGL{Dt(J~Rf1kd>fykuOsuEZ9&$`M$hxWNYj(MFz#EpT~049F=b(+g{<1-'
    'szBA@^m_njsjj@p`$U-5*vT)J812Nh@_sE_v`RMG@osj9g)Z`M=Y)5xM3+j~K%+@V$!*T-'
    'GAI7eN3tE+XjBg53i|K^fYS^hf}pk>rU1a881_DG5XWlD?xpr<}2Rn&AXrJ%JTitz?$_!FGF+T5@f;?AnC88TUp`{{mEBfAWi%aL'
    '6>cJ)?37f@6&Pl2E2~X<XIgcs*6Wub`*b{R{kg&R6p2$(TNs0V=u+7JDEU!I>Mwe@v_K34I-'
    'Wo<#mV(1p!Dya;^MqUM?PhlbOsRYA$dW?AgugJi=>eh#OYLph9%=skc}txPxgt2~&({$%R&PQl9}0D4EU2oJd8WJFPfv$0x1-'
    'u@eoc~h_}0>_t)2xCr5+*y+;ka5`5Jj?2!jRJJT5`o4%{XzGfmY0c(ZU%j6KfSfaBw;!8gC3e9cEsd-'
    '&rGB|UYtmt8we`~_kzKMO{X+*evF%B&MfFi?BJFrh4F301965l38VWaPP`e5za`<SNe#C(vRC$?$evbYPZlYw+`Qs5R0nbig5(C#'
    'serjxajWY%tcgC0===X7M8^Unfor^uQ*4V^g9i{_R;)@CuVF+4y<p|miP!n%-n0@~B8Bzn3r1-VkIc%bkO%;2q-'
    '<TOruOs3kE|ry1Y?M*srPOE9!#+fl{-'
    'cuODNR;Lo!oWjj9vHg4CP~m%~r}!9^uYSIHy!Zl+&Ks4EK;64?0o=V@H93ZJF1BvY2gg7#)*)hgM_sy-5x-Uy51wsU${u$;-'
    'E7mS47+gA^U+$+Q7tJ>%$18-'
    'n`!A#2ToGY*dE429%nPQLZkX^;Dutkk1PrV%X$*BM7Q*R_?4DNMu2z?B=0MdK&Si}hl7qJ_PGz8ZQ_*|jNVr?gbKnik)9u%0fh9O'
    '(vxl&`~t!c>r0LXK!o9q!|Qy|=cXKMu(up*pc1$TBdW%FE>nygskkIE`strxh3Tj}n36#mWv{`fr)K%?P?ZlPm}y*h56bd)o=t0q'
    '1FG#cJqi!opMvpDr(WK@#7zPf;+xEquu0kGeBe{{Tot-z{3=YdpxTKKeY41uJqAXfzaneLpL5dpNz{Xh#&l<|*TkueA;y!Z-'
    '`4>3R@@^X@VsyfI4XB!KOHi}~a`kz0yI1IEpV#K&wx$$SGl_JKIEvMqy(qEHwEEb^1qa^GM-'
    '`)^`8kF1i>D|t3LW9WNvPwqEV5=A#5Q0~eq5qLqd*iXP^v>PHd%!~yWWD`!&DOYCdW|arV9SBXm&Y;Uz+Hi_Ew<nN1~%a<O(h|tH'
    'ycZ7p>hCa2GidGCuP`yv9#S<;=KTNyuG{IFx~z>{4mz%YTouPwDdQb;^o2vS6X{GuZ2sFkWK&F!(A6==2CG@TaEhX+6Nnb8iGgzN'
    '18q29(FRxh0hvWNoZl%1Vn?Co|~|q!swgw!u8`XYOk~9;jT?wMUUJd_L&4M`W#zN4`Ix#-'
    'ZS&Sp;}#saYEp{1#<(wupX`xI+l|;uhF!+a_M9by+2&t^l?k5^t1Ukdef)Yc83s6Xj4f0NZ9k>iJw_5_2A%A3lBg_9-'
    'uI%$s4EUM3i9lW%#W7&DRhwdfki5{-'
    'CD`WatGh>~K>hw{ILdJnQmrSK%P`Bv_0pW{(IMLV%x1IxC2^6|w7<MT}}Tf*^hF%*Mzv#0ByKCsf^~K7Yoa|DKPi|Cu;d4oFVzsZ'
    'l2+Zw&-_5LaMWyk;S4N!;_%V3g?DLb%FSw7ntAWLMom7Ys?-'
    ';n3661@4OWpHZV$R8$lx*l6Go^IuEX13IuXRMC=oKu!Ox36nLcr0P=0^lZ5G9d83bq4m<hQXFm&HZT?z%+w<VUMqLCt!%8Mue7a*'
    'yp{aq(nK%*VT@x39H|(`ij>h7-'
    'nOtHdOBxXn5NNKEnt*#UEKxNYe+#x4s?ldF1CA=9YY_ai<l=ChHwnC5dU=Jit3;4JYVNNTafjtTWk60B8rjw&6@M|VzES{q8e3hy'
    'N!Rijo(|16Q}vNUw>`f{qU=%A9+1AzVb9hU{%4adP%V=s7dr4CJ)jjyI@8^y0a9rDLEDNraPNeQ$(;<Ehp3vC;Va<qf#_i3ikLFi'
    'XPHfKY)E&Adv7A`2{-'
    ';I{v9RaEajF6z)}&+$rp9wp+SxlTK1JJ}NR|sn3@k_k6#rRvN3y=CvF7w~4`}Hv<34)x@)OHBoq?rK>x}5N%lH+3x2S^Z!n(Lp!p'
    '6-qFB6Gz`?5Y9155NP%UFl%Z1C#p)1y&Fcc5yhf{{4Oa0;SIgM5gcWNg8|eks2AkfMDH(pIH8r_&PL0?_F3flbRfU+<;#>-'
    'rZsKxAT+OXk0yMEy*=S(7GvNLxf#-J!_O6zVGV+55U@GIN5F^kb5#(MQ3)qHFLC9!KpsoFVxEU8O`zdhv%T5>nbmJ-FZo~}ykGiG'
    'Y40?hh3p!u1mP@te-'
    'k^=%Aojh?nbVeO?NV=W69DNA(n&wz1=@Fb3|V@CcB#jw5ep3sy&$r$7ArFLe;H9oafm##?8;vXfH@ydgafGu&gMPt!kxhZ66H%=m'
    '4dCqnFb8TAOA9*^sb7Xmoh8MTzF3{Eza9wpiN6^%PPDrtSnnD-'
    'E%9;HdpfR40;)oe2ew8gXJU$kyE2&14v4wJ|men)MQIJdL`yn5x!JqqgnMIGO6-'
    'nQGGLPDC%J;LGs}#e#kU$Hl@JGWWsWW*NSc8OWj7YrHuq44amr0pIPd)7e;s>*j@Rui}setr5qi@j3SE4FL^7fi~;aZD#*MG%@5d'
    'BZK1A^V^ej7PetlVD@7{F)@;c#Zc)<6RHk;z)-9jcB2vp54w~@d>QLLF%QrHg38iZGAQNrHVdljFw<-'
    '^&>mzO+`?k4(WH+0(WJ;>C6*&=Ow+$Afs`(wEQ5Yt)xi7AZ<H=}9anVNG*lcqp2KNMUEJ}_ra9b|KQ!CbCyOW+6^dl}}j?7am99K'
    'NK|E1t)0w8Yds}44zM|hTPQ59s?gYbwBpluuL7xw1$i_HYS=#9@oiFF6^cnOjc(_6e~<de%8Hmw2JwcNS`Rd~h9LvF_=nn;-'
    'i2j18o4im?R`n@C-XRA$wEHL(x3o$#je{E@Fu-hKa9F}Sb+~5UI80s7@leGxbA3lOU<DaH>bM<0#>j6F>9q9yPIkRhrH^G?uMPQD'
    'aK6<@=Cbdj;w{4As!W5@E;|4yj{_p8&YS)q4n)hIXy4H#pDs4RZG|H=MMVVAz%LsV-Ew8t=*0yVJx$C}Md8ZrfH|lOu-PANFZn+h'
    '|KyRyE`nuZYy(yqcnZ}k-'
    'Ooc55;?HR8EpsGG_q;~VmdI=7{3zpJIQ{M4_ce&k|4o0x1onv~H!#^~ORCN71N1OLdq)*dz8{{!%<b=>mulfsmdkAEFnNZ5H#Kir'
    'XZKJ>fGRe07AO7i!{2-7`uRe2@HOD^h-%dyu;-'
    '+=a91(A88h!nIv}>`HEvQqSr{YXU)rXtnc+CT5?ag%K;DM5*j~X9Ddjee|AdQBqyv^qGCI9%kj;Tkx75gOPc57Pyi-'
    '3~op<jtMua}Q?E<K3VHi&wQ+2C>fh@IV+@LZ0tKrz^$0XO{px2eAAlTuBrtl_vy?EHGwiZsp0n&`ue()H2v@nRz#av^L`1Z*w_Zb'
    '-mtKpg>LK!2=;}8MwFIOo$*2;Agx3E|Kt$`J{vV{iZ${dSGOmoXSDW<s2W0s{=9<z|C8f-'
    'n;5p;*D8<fau1}c#u14ntkwFE_7P<k7Qj4l<2l)Be_z62#%;vT1<KGA{CRR6a&C^sSCax><b3&I%BN6tdV4|AeGryZrxE^h2jcO!'
    '5+x32OSs_wpp=6S|BT%G<cobKTg?jGqFp129Omt%S|va>^~>qTK4V<QNUWM)$U`$;7C;(H;qjj(ufG677*h9bV^E>H!?a^$Qe6p+'
    'F-m^zQVjE-'
    'X3?@EkI@L;rgE+($Sgjb*l<}6L0M4U>30tx@4)pm|sDv?*cY@kGVF9wa|5L7jD=}E{{f^DSi(ioH%)X&%&t?_Du?p03@p%obIc=$'
    ')US#|56b#heW&g|(a+Vi~6V9)<*<Vwu7BzL@v)}}}=fRgv)2w*WrR7rE0D6|UhL+7}rFG||85Shi!&faoB@p_to-'
    'f%=#tRk2hXz;1Z&-'
    '$$VS+!QvJgmYNGTt4xK{wHB^2@ax8L{rg)0{@)Ov@gfX}N84>##iN!53_D(%T3K!1IO3tGdn4*dWfo_>09AsFj^TaFlvM!lD96^G'
    'ZZUn38ho|ITK;=Ol7DQagf}@MH0~|3!9Y3{zd4<lh<0M?9K>R2Wkx09#)s5Ceos@br;5Rq84`CZ~M73)@ad3+e|U*3AqDygx#fQe'
    '0h)6ciII-'
    ';fi06FpBcgjEbOOF}5RA7=>(FJD+(0wGI1D11^<bTi|W=JcX5;HxN`A=JKanwjE!R=+0~A4vVsVHpu^+YDVhXC`WvZ9Bnl+1(~Nb'
    'U<Z{q`gl$o7T%Ii$pdt&(%X>{1|1Sm_o_RtLTz1<Ndi$IZQ#b$s>{E^+SV(nvrmu>lMhQYON_!C{!y=T-'
    '9^rUL=9Fy=2QUdTB1*XJDQ*&1H#-'
    'Qs|3n@sqm}b#nr|Wt;l%Ygfi1W!tz7SJd7YdtklQx{PA*sh}w%yr5zuP&TuP`Z>2b^_dbkVL?ODNCLwbav$FT5TLVs)xI;G!1L=H'
    'bL9~6t(y$>gJtF=mCqs<sajhM9m)rxR0#()W3+EjH$nIssCbVSN3vcmcv?2`R`oj#8)BVj^B_pxzP|h6l>Y-'
    '{e%9zD8!Mg|{h@Qw!&Jk@@*qF9ktjsfvcCeh5rzze6*7Y#3SJ=lC?6%8qYaFw>Cz)-'
    'B`Z)ntTZ9cyjZ#gdK>Y|Htt~6+$s}co25q1k6pPw1?O&kS{=w8wZ+#wFizmxr+1l!2f4`t0cpD|@R6s3z~KB=G>AvljtE{UPcL4I'
    'THyz^krUFFCQ<&dqv?vY*OT}+@<Z{0eth4G6_M~(QTEOf$yH)a_-nj~QyQPCTCUIZQ!P!a%Dk}LT2jWvy`LweuC^e+%W0G7G*3#7'
    'J^y~E^+`mMK*)<olAx28aJYuX=97A@5)GHdf_R&>MphCbDb+b5AXA`qpyA8@RbLN-'
    'r5u+wSPEGyL+~j#vX%Wa*77nOQp=6Q@On&+9nVSBmAS=FmEv|BA8d8Uk+~Si{F8%Jx89)urqq3oI3#^WPF)@^E}{6>)X~rEKF7qH'
    'g;gnd#|x_jowSkrjBX348#$O);llS`s3~oHDPM%ZpMFa%aF*$nStHXu%pGgKF^ltVDv6BoZ}}_rc_|y7mH|pX8iPO-'
    'J?aW55kNfhMDSX;B#mzhs+i#BB)V_!GNe!jqLEdMzKQsTT;ovhG234pE*_~`7N8q=4`Ec)RDN)9r+w~angPTqNU2R6Xs!&F0WVGA'
    'C8SgdN@rHd#Q~f5#_5%K`AR`tb#s#NvMk0t)v7idF_I-_V?Lh>zSRpeH1_kQ2_GIOU~3E%@Z5MthOOaHms7drRHC6nY~qOdkO@)0'
    'wFc~w@*41vR(i(?XHl6-0q;>!XM{xhQ<--mg(dD_$;7-'
    'CFX^cwe%;CtN<>eXr_Bv4Ot=<+1DFK{E@2~_H+Yap3$b~WfRTtsEde%(xM$`eT+q2G?2?%`oZFuchB>srm?jJAn&dG_lp3=}aSl-'
    'zQMDr47tD~%u4|PJo`XSyo3bQRmSs$^Y*9KSo_SP=mBKut7+%!UgBe-'
    'D;9E(Rj3m&}0#N}DXh;@SRpy<4$A1W*alx(${7cg=Ehz7j1XX;xj|W;wT&aXelz3V`Q0{3hm(=#KiY!b7G?JPd8JCLj!TlAE;48w'
    'aD2-'
    'KV#;QV)8Y5sYUoYLtGiRB9h1ZQx<O1W4Y8*(KNV*qOVVC?~)eNcd&{*259rvJ6FD0%eiC+b~HQg0Tm8#j9ls9r*vs3&{=PZ+RwlI'
    'sH<bNR&jOjNp$UiM9N_FEj&sRkqLCM6S%O#Ud=oKiCW^7N=l<i5Hvpv}<X}d99c%72|zxMiqU2Y)Yoz=3dtMimOo&mjEz%H4Dekf'
    'WC?}*rvZ%|P~7-'
    'y38N>kx+#PyVU2KqcYC(CHuvP+8hY|q8H(W2ht1!B!Pon)lkcJxe@dOt&FwdcWylHLj%QB5}Ux{^AN(Nv`dsO3zGP~7JB;GzM6Ig'
    '(Z`T_W<rlBU(4i<KXB?SS{-2KVY9at0b*m8M-'
    'XRj17+7(Geo$|r88$gEQtYv6x{6_DESrr(e~o}!tD@}1bS_5qsd(NE>}87+Vj`Aq&FcihtdXIjAAxVrsLmLU<a(E8+IFV6ld<ds?'
    '>H4zQbx~MN1n!J_a;4w_ViSxO4d6C&~f;^jJILT^giTv)b6jF7AUu*f@pi)JE-J-aSrwc`Ro6<#Yvs;KjJ6bD-PAL(+rfvmV^GPP'
    'LuH~hl)R-'
    'm0z*|ScB;25!@&a5`GbOn8a+?U(0T5NRXCjRDOtjISOUPsBqbz<o8C!soE|V2r*_VKv0qgd(bGLcR42^=gi|)d%<tCPNxr(M1dWe'
    '{SvRV|r&?Z*q%cis>AG+sX`h!n;P63G!ubaVRvv;_Q|Dr?SQbgNqOB=k3gr3zfWQIu>+U_9h{%BQCT4@M@hdX|>Iy^Gd-'
    'ao`OjkeqoLAxSKg%0l2z3hMbJfWRV+FTJL97Vo<IOkOw;!kLYG{_-V1ynxOG|v&9c}$y=>-Usrnbz-bPa8*@a)A`&=FA6T<(``6h'
    '~l1T81*lEd}(UaHok1VkK_J7u-'
    'cJ%W!$S^oDW9;qLmH!#^D@1&j&Y}QOHiz0iPz~M28L)i8ZK&xlFmYMzz%wedE#J^s9&lZ=Ca^TyQviD&q0IJ-'
    'T2q$J|*)<NNfW<&EoV<!r50J4bn>VfNO#7WZISFpFa5g~1H%v6m3{l|C?a&n<NxlizD;FEG=S2I=Vmthjmn5_sCxtK{v8Tpx-'
    '1O<cEu`ihbU;zk;b89jlhOYJbZAyEc&v!wXJD!Ri^n`yH}W<pAcv`sZTdPZb69hX{x)Y&4ZtRGF#|2ffg{<#N=B!9h!4%*mIZ6w&'
    'm7bxRJc2kxW^OPipN5%CZgnln7YIre@{Q5C@TOgX+p*s_4V({+<^#3;=;v>316^>z@#|H5(Kyhy(mT4ci8l3a22ez0s#5Kec+B5>'
    'aLp}i}ltc~`gNb{hALXt%iXL(_PZT&rI70KRsrU5%lXCl?(i#5)vPM%UANzyurQBl%+*+w^nwGDa-PuWt^LYEr-'
    '84XjGemhwe3k5-'
    'qs@}HR+p|^fO$?=iCK8ZF`qTSXpgi*)xx`r7mQ!6k=}yYjMhQZ`oh>sgGP~QKM3e5%S=o?y?}_j$&>=wU5O9@O`~)tBL_#xl>H#)'
    'cb)4|c=D%{2RD(4S*MJ$J%OK0;c(TM2F(o4vw-'
    '%+f>QLK8%sY}Crn;#A|{|3L{t)S2AGide6ONdPLLkDxC|LLZ~$&=lqe)<@ahxrZYUpAZmFoimn?F5Q%~Q)j_Rgo(LFrex8!>*0mW'
    'EaU+CJJ;0x~&D7{KyhwFB9ob=mvRAh-'
    '{SyuFvto#9$UV8&hE_iea*rwOt*y8#daq|s=kU8580%!CJzuAgX0n>P387G7x&K~<Kq^EWvQ@fC$8$urV_tp|S13B*bB`3LRhZiS'
    'Hong+Z6jVBMJa>(E&RTpUVZ28)0j;)<6Y45h=qrAySel<q|D&6jQWB;#$q-'
    'N6FK@2BAd3o#5M)<!cVkg;u@nK>e6!T<lr}DxDs7xMS$0#@D_Y}(k)cNdo0%BK^0p|iO5~7RGTfnOTt$)QvNU#(o8Ff7!%j;FmQ|'
    'WYG_(I|d;be>^$;&9t*2lbl&7j&z(Ls!<#%eUh`7vpOySq`3M;PR#8p+8(~oDHq#S~2()_ttVfDcte0DK8Np$~+VrRPc7wIil1^Z'
    '9#*WgiPBMtNthJpSqSuCgAE03U>-+g=1uKUEl)L$k5MOq?_n(!jRbpET@JZX-n3T5)Lxd<nJ&EuT77x}vTb>Muu>5eY4JA*i?d~-'
    '0}IBHXzvYNj@G<Okc?l3TKW<0qtRyMR|5J%dr#_u*QXuDo^f$2tmF1g$^dx7Z|7=@RcD(Zdt3I^Xe+b<sH#BopaCl&1QeCD|Wetm'
    'AWRQ7=9bXO<UFQz6m`cu}c(ikL{YCmhl^Rk91FIbQadeuELjJ1ZiC@;RH_L1LGo4MBBxW@k6>GFN=>LU9c7BkhvDHn*@iBxjf9)*'
    'h1BQ@<jirlH1M@B!|lcAD4<s)fz{<(j7k)s&?33*3Nze0BYM>XQM`xfyprxr~^Fke9<Nvgq0^qe9FE!b@B=g!JkV?}Z5qW8SQ*vY'
    '?-E=Tph0hqG00{'
)
_ENTRY_B85 = (
    'c-pNyYg5}wvfud?bDgRyIq?Gs;pDQ{yXAnB;|j4U+sWB1%T*2180#QQE{)7%v-$7a-'
    '7_PNBx49%K457cJ^h~Uo>8OG7{{V57fHrMoTc}xB#bk*N|RM0dDLMeaU)U|#y82eV7FnmNY)vXV#QOQ3HI%5%J>RK-'
    '@w?No!KIkY#v5}twcIc(xqf^!fuoFnk6v{m#ZYrI&8KOY`)IcsbC3=<5`lj<yvMiGll=Fi2GtELr{QG2%`Xla2|?)t+;>9uLPjOI'
    '3U3zXiJkMYbUocJO(h>2Wa<<#{o_T%(I2q5qI3r0F$^Kh)7%k;sMK29?KBMFzEvpED+L9!%OG^;N!Ewq(2)^ytB#pcyRJ{GUzOW&'
    'Q7Dz*il5VKV=e|=R}$(;8}RL;@QG9q@s9|$vXA(cbV}lf4-'
    'O2qo2g{@M>pg$2%EL2FEk_nSN$HmOAd=j%0Ao+yA<F^Y0t;5Z^4t`EL7vF5blIr|H1lK+Ks3z~<89Sq6lyGvOj;&7&O#e?ZDa@Bq'
    'Xjm|XHGVl<eA0rETp4w_lH(C0}MCAVRG#lj4w9P@}pVSL>|wjj)T09Yon1MG}xtrotGy~)XVbo!nBTfH5hjt74q9GlnC_~ZEWbo@'
    't?K!5@&{P6EmfC>o(bB_FS=Rq^?1&v$l&J-'
    'wtz7{)tSM>8aivNMh;hec~qNLE7dc)~t@X1x{b!6r(lRz|ClIG*hpEV{BH!W7YhH=tl|J7qIt(y<X-'
    '>82B#0Yprq%_%I+2KL+s5Bzwp#&?#{y~~1sp||zv&naF`hEHl&_02;;q1Fp5S9wCt5`9o69^v&aUCxGFm#%LMjEbMHJIcVS=<Ib`'
    'B5ST@kuKX8Q#68?8>{0693xEz%xY2)x8V!g}!K^;-'
    'C%ooGv^$wtf*RV1Ph(cm?Zas60U^ui5aV8}lW&0Wf`D*tc4UDND)q3Y825sn=m|nA3%|yM#{Hfp>+;juvc5uBW5VE>ISc1|XnjF5'
    'iOmU*dAc(<_lF#X==V?The1fu}L6RLsM>I?jNl?5_OKv2j%Z0WF?p5{=DO)1QFg@%YQx>EQ3)htu)#XYXY2ZFoG;XfAo?FVJg<&U'
    '_t3^y*q_(gA<RSHjg8HUY)7gd;i=-k$f{A1{6!{5-'
    'N>_kVUfZ#4g15I_#L<gpN76I@o;J^#NhxTWUboR%iGY3Ns2f~OyWBYF|Ns8%i5AX4yNA#6511-'
    'o;VChL`S^A&4OQOi?H$RQ(EY<Q-Ez#;`2HPR4I57h-'
    'QR7=}V$>>xh?n0SK8@U{&^AaB+B55Hb*K?((&dR~%5jk5C9o$@iB{+*YPCk!px1w8xN+A@~Ck-9-'
    'z&+oCt%>%h*G;EY^MdwYPH4$p(~>oAKFdeO>*XbF4cm0VVs{A*H5YUQ5jx+ycp)yfIvC-'
    'D7ewH$Z7YxKhm81*kWW>PM=Z%x7O;s&1;)9_7_~3!gQLQcBlQEeZvk#T-R2*%F}Gx7=tvPBMSdj;j5Sv`-'
    'zqE1bXnqm?nbfXe<o>*En(+R$sMg+2(3$O=*U$RX0CGttI(VnHYPMj+TdtwgTaMVba%d?;d{Z5(#H4U^s}4Kv11xmYF&o`D)VlYJ'
    'EcJ}?Z7Z>Z|krWkj7Df-jZJncynzYJcW_t$?(%~q_uK1J~29pae~hwf)9Njd4vvx>F$=0pkVS=Z(~C!&$h6wF}D;|l<{hAjUQ_<L'
    'uE2;K1LB=;^&n&U=}pnNo)+2VDB!7QUgoGklKN%fO|6CFWRw@tD9pe<bw8W6(0V=?)~W04UQ~lzxGlu@J>9w-'
    '=Z^NCYBFtN{uTtyrdgs5!eiNP@4NJo7iFnHRN8ZGtXse?rJ<Mv-'
    '>qS=;hhiMz)vzC8ckr7I0#%y2q=qvg%zcGp=eah{`q?<a{f0{487wFP5ki50K!M^Uw|LNX`T67K74<W+Q2j&Ms41X8+@}tCSZ#Is'
    'Gr#Qgo(l8wG8@Eo=9+Vhp`;LTdbqwTIpI830%&H-'
    'hq8EFG;ftNt`klO;1H9l=vbM5ydls&@gTFb>2@#8`BR?x~_h0nCES1)~a>RoybhFI^VFlAO;Gx6TgOuksjSEJd2RyRBL<Y2NvzB$'
    'wiOSf}&6CPW^l_{4){C}mhwwji9xGM$&izFo)y59((k@FKpB{RP+}msYbb9_ZS^C14F{Kh=X|OZ#ScSSIT@3*&iG6bwEKvC_FeQ+'
    'itz8i}D|J_r7W!1OSI5aUkO5_HKb6f&<EfE&o}ArmPUF)<<_iK4`+i`Ds8wzqrm{@rhfkE_#{@mKIlqmN?~<slT@XwNV5NCotkYL'
    'xj3D$c`Z$V7`Z7Ccg$H1?QSkhNJk<v?83NZJDSpB5EV@l-V*5e2~eG`w111w%nS-@VXlH(RD@FK?=1qF^-'
    'H|FT2FNCJl~Z?Ofryt(L*qg=}j6wveii{|r*Q-y0vO6B?z(vN@s(}e|oyZG_;=a!T+;>*i`t75?s^A{-'
    'w=`Otj7h3Fnvzg;}a8b?$lT58zN}0P3ZG3qtxC5c@l1z@qk__qT`J|Fwd9-'
    '8yGQGb{^xHbo#rjXAyqG}b7q8Ff!7DZq84p!`(NC7ENM!hcZYRD1uJ}nBkTnYKFBsv&`ruF?rkrapV*%5CiSY=NNQlaj&HzCA8P&'
    ')s<^q3@urEb+D+H{3n-'
    's+gX#vX?9GofmXdYo{`X1|n5|0`PU5;Bq)+tL<cK8Z60#|8?WfIz4!Ai{&N~mDr!<r3FrCFp;H({EsdDLR!ZY9u9Qw9Z6OV=@&pp'
    'z|EbLoKGp=p&SKDN<sbGt4voPd>I7*8Q~DPvYgFW?<1R<(jw<2XU3um#A&R9z!^QS~|mrfE`81r??Md$7$=^fYbyHk{19_D_{onc'
    'z~~Kv<oMSQmCFtW-%sOCNkd{;<S=MD{-'
    'NNIn#AVnAE~E9c#%=7$n*1L+GyDjOmFH``5i_xGO=`KXX*I{rF29(ZT{$si{e(8l~kGrnxZpblur7=}<kuox*sWzjkUf=>Ao1#M$'
    'qm+cc!oP0HrnJtYndTMD@F)fe$S>-'
    'BuOZiP(v{Mc9HxTVsAa=_jyH%J}!f(EudX?wclqZ)EzukzS98Zw*0l*YPSzv`sA~m>{F;e}Y>bK2=O<vpSbR1lx7^A|G?vu;SM$c'
    'xyTZ#dmGzbHT;N*5E@PLacNbKcqVnbP_JlH1BNIO+$UqmftxlZ$<%AEq$U)Ytr<MFq_r2lEKk>-'
    'wsh}pds_dUJiB05Lp(KKS<Z&N&Bm!daI*TMp_NMOqVrWMd&c&Q|%hG@^3a|$bEs^fs1BhXq)bP<69F2%oO@a1-'
    'Nuz(>#uXOBD2h|41QfmU)U}U*;>Y~_gJ=<h38cb({ladKl&W6d8lQrmExG24w5;x%EQqA&D0Ibep39}<i0Zk`XFioc|1<mWdi&Ev'
    'uG~3Qo++_#oN$%!PuF@;l*q_ZN!w+9)!|}+XQ71}0v((#~LhEDHlCe`}`%9}UyRI$lA!98TtJbZX<%0zp3*y%5X=R+Xg;u*Bu2`!'
    '*))IJSCKfyj@jp0~)M4|~To3+V*qmR;=&|a0RGO)@)r&e?S(t8uv7tKim>g;_u4?6>tsr!%r_!32d5TL4dVegUWx8f!d`n9kgQ+v'
    'Ls$A5ztL`J<mh|JN)j(Ayt97oTHZ*ClVD#l8aDR|*dwWrGS_ZG5mdsy4x>3}R7M$)v>AHMTDN5#aohG@BAw5|`G6Q(}Sn+^X3sm)'
    'X4mfLnA^dAfe{L5^B&Znj7Z&ULTA4^8>&Vz(GzK+e+y(|=JuI7Gl*Pny1@KH2%D-n=uI-'
    'otiz=^6TE)7hA+K8&xZ4HZ_#(gE)m|w_G!;vZoTghKCb+@M6A*;F)8}C2^H3wayz-aJQ6GRk<zh<7=h)#SY-'
    'a>Ui?6axQJ315s`|SjaG6uVBOv{7H>2Bpq8re4%Ia2T;=fHwZWx*~EN8o=lI7L$47ogO(|w0L2uZMhwH8$n`Idu|En4dko|K#PPl'
    'x?!h2Q=<t)yetI*)EM7KD-q)or==wB8@{oHoqDby^LNy3HyC@|)KCV?MJwUpq~!rS33|z^c3extZr@3hns!{^---'
    '#1fnKs^?0ZE?DU-trP*+2lS|_w)X|sRzEhi2Ao@yFY+_7xO}7Q35JG80T8{9KAsMbXEiD|tz|8hC;wDt)@fm0WX{&BNSIovsVr6E'
    '5R9m$$nv=qH(+B9tXdeb29w#ryXJU@;!^20pK=~D9G8z_^*P87M+0+?j36OawV9E3sMoTpI+UBj5Tf06U!$XJ<tm}~%!}s6U9<Vq'
    'TsYLH1$4wIT`>DAu=OlSB2Ab^*6wLpM-QFJ;B?TR4oYnw{+RU7%GlcH8lAK6v){)fO!3z;b@XrdGK}-*D7>r`RP*-k-'
    'TOoD@SqK*xV~#&#cP@*GAf^`iR$<8paLSl>Dz%FmI!tonFTndQgM6j<|n>tCwT4A4QO*((?PSG)+SX?fG7zn)e{N`XM@qnaP+BSc'
    '&5gUP&U+vn&o?<5E?vi+M|VslNntORu#ss6w$n!ACds4AOJ}MnBOy3R`FyB1S$CPs9Z0_2`Y7iK6L?D9pU^|(&{H4LTO5rMAj*tt'
    'GN*T+-Tg<*3mkt$sBbQ4Uj;;-*ix3Qncx;|A`7Dv%zF!9grXhNFZw#k3mcMN>!tDaMEZoeREh}rmFc(s{2wdjMBzs_K#?t-'
    '2LQ@x&J9zw)m2U@+JMrr>|e&aoW(gPS7ktESjR7BF)CATDgWC{*HB%kS2CgRdZ@|fwH}(0if>Z_gJH=V#bb)0ARNPQ5wyD!x|kLq'
    't5=&C(sqD8?G$$Um||@md;SqHg>ihJ=3h`v>kgr>zbW8EUlVSad=8sRRHMYes?w=pW4;2SL_%MD=0EnevF=O6Hvk^qU?Z)JK?W0e'
    'i@;%p514QBz7c3=$9#n)B$u~2OO_hl<+_hz1Y=NdQ7Lsw4<`i1Z<D{SX^fT&p1t%cp`$IMNj~3)bADJ%S$kVWG$IC9AzlxSmaWuu'
    'O}j|P7$&M6A7I$fMJ7E(Gk6w-rL|Jw)pzOaoD!3N06v38`fa70b{m7D%L}T&?;Q|ssK9IOQFteG1tKhGaZ~kc>?_^Yf`sf$-'
    'b>*hdyx;cc3_an6Y`puW)=Ifb)2?FCs|JF6pp`j;v9pkcR~VaFv#QQxfKj0GtB~0a*#Ekm6S`Fg8S|tiFj+U)NwdNODAYu%TugR3'
    'xyVVWgg--lVWIfub*Y4BSff7EA6-'
    'nwtY0=)NgmASeQUbwdIBd%4F``~ol6V+R}Kvnp%P+k2E}IqkLuv5lyJ(3CC8@+r49t{leYKs&2s<=Ub+Lk>#W2IZwsQI?cQ$I<;?'
    'DAOiq(Y}Q+7h@&-_5g!0wvK_7B7m`-'
    'Qj&Eo?qNgVcj0oqR7Ydxlbz1wbogaB!%tO)rx080?Z1EQOsqEd<8=5BoO;M!v%TH@1O4;MaE`|l0JB?5D$OvUG$l^BM31Wp$|<vo'
    'm)q?L3yamwQ0qYs;c2e6<l8I0R&5Y-'
    'v#1B@JXST_8hPXap6NtT6OETJ5U<ue#e<U~1nQfoay9z__83fimG?@VStzx)O2B2@_uOK|<FWeAZ1`>9QD${EhFs+PqbXq7T9lh2'
    'Z)eGN#Zd?)0zZHc4@59kT`qCJ{%}aHqm0fQtdK>&7{=znJeu93|BvUa7EazGky%fp*V1i8ux@%vu0f%8-oPrrS~e)@Yn)BS--'
    'grSc+@}jrpLbzzVy9s{nO!zhu@kt9?1lC_%azxzn;z-)*X@*??K?VeVt&vTuJwbS@fue#Wu1VGUhA%rqYuOzW?sfo?hwM4ySPI-j'
    'b)+BDEmZ`!0TN0fyyE51fKj2Tiq4#&*^BA$Pu5W$Xi&VnB}|Y`dg+rXpyRVtH?&FD}gtmWey6@lz!VxO#&XO)w!WF<R9=#qY*H2B'
    'UXxz40HT!Nfc6pUu8b2CCLDIBB$~TG+!)8nPpirZvR(VC%;Zdpr!yJP)nC;dvMcc%D`Sr2tdf5e9c5UUh*m0eJ8q4Swli'
)

def fixture(text, expected):
    raw = zlib.decompress(base64.b85decode(text))
    assert hashlib.sha256(raw).hexdigest() == expected
    return raw

PACKAGE_DIR = pathlib.Path(__file__).resolve().parent
SEVEN_FILES = frozenset(('operator_once.py', 'test_operator_once.py',
    'approved_binding.py', 'binding_store.py', 'test_binding_store.py',
    'INSTALL_CONFIG.json', 'root_operator.py', 'test_root_operator.py',
    'README.md', 'MAILBOX_STEP2_DESIGN.md', 'INVENTORY.json',
    'launch_once.py', 'zen53-preparation-preflight.py'))


class Clock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeHost:
    def __init__(self, *, busy=0, fail=None, stage=None):
        self.busy = busy
        self.fail = fail
        self.stage = stage
        self.events = []
        self.spawns = 0

    def schedule(self, required):
        self.events.append(('schedule', required))
        if self.busy:
            self.busy -= 1
            raise ValueError('ADMISSION_TIMER_NEAR')
        if self.fail == 'schedule':
            raise ValueError('ADMISSION_JOBS')

    def preflight(self, package, recovery, archive):
        self.events.append(('preflight', package, recovery, archive))
        if self.fail == 'preflight':
            raise ValueError('OP_PREFLIGHT_BLOCK')

    def prepared(self, archive, package):
        self.events.append(('prepared', archive, package))
        if self.fail == 'prepared':
            raise ValueError('LAUNCH_MANIFEST_CONTRACT')
        return {'source_to_origin': {'origins': {'8:1': '8:0'}}}

    def early(self, item):
        self.events.append(('early',))
        if self.fail == 'early':
            raise ValueError('OP_BACKING_CHANGED')
        if self.fail == 'false_early':
            return {'headroom': 'FAIL', 'controllers': 'PASS',
                    'lock_metadata': 'PASS', 'scope_absent': 'PASS',
                    'source_to_origin': 'PASS'}
        return {'headroom': 'PASS', 'controllers': 'PASS',
                'lock_metadata': 'PASS', 'scope_absent': 'PASS',
                'source_to_origin': 'PASS'}

    def launch(self, archive, package, before_spawn):
        self.events.append(('launch', archive, package))
        before_spawn()
        self.spawns += 1
        if self.fail == 'spawn':
            raise OSError('synthetic uncertain spawn')
        if self.stage is not None:
            assert self.stage.read_bytes() == b'unchanged sealed stage'
        return 'RETURNED_ZERO_NOT_ACCEPTED'


class OperatorTests(unittest.TestCase):
    def test_allowlist_covers_every_pinned_fixed_rejection(self):
        # Parse the exact source fixtures carried by this seven-file package.
        # A newly added fixed failure cannot silently become UNKNOWN.
        sources = (
            (PACKAGE_DIR / 'operator_once.py').read_bytes(),
            (PACKAGE_DIR / 'launch_once.py').read_bytes(),
            (PACKAGE_DIR / 'approved_binding.py').read_bytes(),
            (PACKAGE_DIR / 'binding_store.py').read_bytes(),
            fixture(_ADMISSION_B85, op.ADMISSION_SHA),
            fixture(_ENTRY_B85,
                'd7402cc58c1871294fedc85d3c484ed7ca5858ca598d78d8f409a5cf903cd173'),
        )
        codes = set()
        for source in sources:
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                arg = (node.args[1] if node.func.id == 'need' and
                       len(node.args) > 1 else node.args[0] if
                       node.func.id in ('ValueError','BindingRejected') and node.args else None)
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value.startswith(('OP_', 'ADMISSION_', 'SPAWN_',
                                             'LAUNCH_', 'ENTRY_', 'BINDING_')):
                        codes.add(arg.value)
        # Pinned Admission.number() forms only these two codes dynamically.
        codes.update(('ADMISSION_MEMORY_MAX', 'ADMISSION_PIDS_MAX'))
        self.assertEqual(op.FAILURE_CODES, codes)

    def test_every_fixed_code_is_durable_bounded_and_never_spawns(self):
        for code in sorted(op.FAILURE_CODES):
            with self.subTest(code=code):
                class FixedFailure(FakeHost):
                    def early(self, item):
                        self.early_check = 'SCOPE_UNIT'
                        raise ValueError(code)
                host = FixedFailure()
                with self.assertRaises(ValueError):
                    self.run_case(host)
                raw = (self.audit/'99_BLOCKED_OR_UNKNOWN.json').read_bytes()
                report = json.loads(raw)
                self.assertEqual((report['stage'], report['attempted_check'],
                                  report['failure_code']),
                                 ('EARLY_FIRST', 'SCOPE_UNIT', code))
                self.assertLess(len(raw), 512)
                self.assertEqual(host.spawns, 0)
                for child in self.audit.iterdir():
                    child.unlink()
                self.audit.rmdir()

    def test_absent_scope_query_schema_and_status_variants(self):
        # The pinned systemctl 255 show mode can return only generic Unit
        # properties for a not-found .scope. It need not expose service PID
        # or FD-store properties, even with --all.
        admission = SimpleNamespace()
        exec(compile(fixture(_ADMISSION_B85, op.ADMISSION_SHA),
                     'pinned_admission.py', 'exec'), admission.__dict__)
        keys = ('LoadState', 'ActiveState')
        expected_argv = ['systemctl', 'show', 'unit.scope', '--no-pager',
                         '-pLoadState', '-pActiveState']
        for output, error, expected in (
            ('LoadState=not-found\nActiveState=inactive\n', None, None),
            ('ActiveState=inactive\nLoadState=not-found\n', None, None),
            ('LoadState=not-found\n', None, 'ADMISSION_PROPERTY_SCHEMA'),
            ('LoadState=not-found\nActiveState=inactive\nActiveState=inactive\n',
             None, 'ADMISSION_PROPERTY_SCHEMA'),
            ('LoadState=not-found\nActiveState=active\n', None, 'OP_SCOPE_PRESENT'),
            ('', 'ADMISSION_QUERY_STATUS', 'ADMISSION_QUERY_STATUS'),
            ('', 'ADMISSION_QUERY_TIMEOUT', 'ADMISSION_QUERY_TIMEOUT'),
            ('', 'ADMISSION_QUERY_OUTPUT', 'ADMISSION_QUERY_OUTPUT'),
            ('', 'ADMISSION_QUERY_CLEANUP_UNCERTAIN',
             'ADMISSION_QUERY_CLEANUP_UNCERTAIN'),
        ):
            with self.subTest(output=output, error=error):
                reader = Mock()
                if error:
                    reader.command.side_effect = ValueError(error)
                else:
                    reader.command.return_value = output
                if expected == 'OP_SCOPE_PRESENT':
                    result = admission._property(reader, 'unit.scope', keys)
                    with self.assertRaisesRegex(ValueError, expected):
                        op.need(result == {'LoadState': 'not-found',
                                          'ActiveState': 'inactive'}, expected)
                elif expected:
                    with self.assertRaisesRegex(ValueError, expected):
                        admission._property(reader, 'unit.scope', keys)
                else:
                    self.assertEqual(admission._property(reader, 'unit.scope', keys),
                        {'LoadState': 'not-found', 'ActiveState': 'inactive'})
                reader.command.assert_called_once_with(expected_argv)

    def test_fresh_preparation_binding(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'zen53_fresh_launch_binding', PACKAGE_DIR / 'launch_once.py')
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        self.assertIsNone(launcher.NONCE)
        launcher.bind(self.approved)
        self.assertEqual(launcher.NONCE, op.NONCE)
        self.assertEqual(launcher.MANIFEST_SHA, op.MANIFEST_SHA)
        self.assertEqual(launcher.TOOL,
                         '/var/lib/zen53-owner-tool-' + op.NONCE)
        self.assertEqual(launcher.ORIGIN, '8:0')
        self.assertEqual(launcher.BLOCK_NODE, '/dev/sda')
        self.assertEqual(launcher.SYSTEMD_RUN_SHA, op.SYSTEMD_SHA)
        self.assertEqual(launcher.ARCHIVE_SHA, op.PACKAGE_ARCHIVE_SHA)
        self.assertEqual(launcher.INVENTORY_SHA, op.PACKAGE_INVENTORY_SHA)
        for source in ('operator_once.py', 'launch_once.py'):
            self.assertNotIn(op.NONCE,
                             (PACKAGE_DIR / source).read_text())

    def test_reused_source_pins(self):
        self.assertEqual(set(os.listdir(PACKAGE_DIR)), SEVEN_FILES)
        sources = [
            ('zen53-preparation-preflight.py', op.PREFLIGHT_SHA),
            ('launch_once.py', op.LAUNCHER_SHA),
            ('approved_binding.py', op.APPROVED_BINDING_SHA),
            ('binding_store.py', op.BINDING_STORE_SHA),
            ('INSTALL_CONFIG.json', op.INSTALL_CONFIG_SHA),
        ]
        for name, expected in sources:
            with self.subTest(name=name):
                path = PACKAGE_DIR / name
                self.assertTrue(path.is_file() and not path.is_symlink())
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                                 expected)
        self.assertEqual(hashlib.sha256(fixture(_ADMISSION_B85,
            op.ADMISSION_SHA)).hexdigest(), op.ADMISSION_SHA)

    def setUp(self):
        self.approved = {
            'profile': 'zen53-python-diagnostic-v1',
            'nonce': 'a58b17a6fcb2a32cba10e177ad394375',
            'attempt_id': '123456789abcdef0123456789abcdef0',
            'manifest_sha256': 'e1d6b6acb299d65d862fdba452e5ecf3d10c83d77c26a9536808435ff782ce48',
            'manifest_seal_sha256': 'b69f9cac196f6f1c85107bc1cf41608b58d091e9986f20f862bd734636f737de',
            'tool_identity': [2049,171075], 'stage_identity': [2049,171077],
            'manifest_identity': [2049,171269]}
        op.bind(self.approved)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.audit = self.root / ('zen53-operator-v4-audit-' + op.NONCE)
        self.stage = self.root / 'sealed-stage'
        self.stage.write_bytes(b'unchanged sealed stage')
        original = op.create_audit
        writer = op.audit_record
        def synthetic_create(path):
            self.assertEqual(path, str(self.audit))
            parent = os.open(self.root, op.DIR_FLAGS)
            try:
                return op._create_audit_at(parent, self.audit.name,
                    owner_uid=os.geteuid(), owner_gid=os.getegid())
            finally:
                os.close(parent)
        op.create_audit = synthetic_create
        op.audit_record = lambda fd,name,payload: writer(fd,name,payload,
            owner_uid=os.geteuid(), owner_gid=os.getegid())
        self.addCleanup(setattr, op, 'create_audit', original)
        self.addCleanup(setattr, op, 'audit_record', writer)

    def run_case(self, host, timer=None):
        timer = timer or Clock()
        return op.run(host, package='/fake/package', recovery='/fake/recovery',
                      approval_claim=lambda: True,
                      approval_recheck=lambda: True,
                      archive='/fake/archive', audit_path=str(self.audit),
                      clock=timer.clock, sleep=timer.sleep)

    def names(self):
        return set(os.listdir(self.audit)) if self.audit.exists() else set()

    def test_wait_then_one_handoff_and_durable_order(self):
        host = FakeHost(busy=2, stage=self.stage)
        timer = Clock()
        self.assertEqual(self.run_case(host, timer),
                         'RETURNED_ZERO_NOT_ACCEPTED')
        self.assertEqual(timer.sleeps, [15, 15])
        self.assertEqual([e[0] for e in host.events],
                         ['schedule', 'schedule', 'schedule', 'prepared',
                          'early', 'preflight', 'prepared', 'early',
                          'schedule', 'launch'])
        self.assertEqual(host.spawns, 1)
        self.assertEqual(self.names(), {'00_INTENT.json', '01_EARLY.json',
                         '02_FINAL_PRECHECK.json', '03_HANDOFF.json',
                         '04_RESULT.json'})
        result = json.loads((self.audit / '04_RESULT.json').read_bytes())
        self.assertFalse(result['accepted'])
        for name in sorted(self.names()):
            record=json.loads((self.audit/name).read_bytes())
            self.assertEqual(record['job_id'],self.approved['nonce'])
            self.assertEqual(record['attempt_id'],self.approved['attempt_id'])
            self.assertIs(type(record['utc_ns']),int)
            self.assertGreaterEqual(record['utc_ns'],0)
            self.assertIs(type(record['elapsed_ms']),int)
            self.assertGreaterEqual(record['elapsed_ms'],30)
        self.assertEqual(result['cleanup_state'],'UNVERIFIED')
        self.assertEqual(self.stage.read_bytes(), b'unchanged sealed stage')

    def test_schedule_deadline_no_reservation(self):
        host = FakeHost(busy=1000)
        with self.assertRaisesRegex(ValueError, 'OP_SCHEDULE_DEADLINE'):
            self.run_case(host)
        self.assertEqual(self.names(), set())
        self.assertEqual(host.spawns, 0)

    def test_slow_successful_schedule_still_blocks_at_deadline(self):
        timer = Clock()
        class Slow(FakeHost):
            def schedule(self, required):
                timer.now += op.WAIT_LIMIT + 1
                super().schedule(required)
        host = Slow()
        with self.assertRaisesRegex(ValueError, 'OP_SCHEDULE_DEADLINE'):
            self.run_case(host, timer)
        self.assertEqual(self.names(), set())
        self.assertEqual(host.spawns, 0)

    def test_non_schedule_error_no_reservation(self):
        host = FakeHost(fail='schedule')
        with self.assertRaisesRegex(ValueError, 'ADMISSION_JOBS'):
            self.run_case(host)
        self.assertEqual(self.names(), set())

    def test_pre_audit_schedule_proximity_activity_and_unknown_markers(self):
        class ApiNear(FakeHost):
            def schedule(self, required):
                raise ValueError('ADMISSION_API_ROTATION_NEAR')
        class ResourceGate(FakeHost):
            def schedule(self, required):
                raise ValueError('ADMISSION_HEADROOM')
        class Activity(FakeHost):
            def schedule(self, required):
                raise ValueError('ADMISSION_WORKER_DESCENDANT')
        class AmbiguousTopology(FakeHost):
            def schedule(self, required):
                raise ValueError('ADMISSION_WORKER_CGROUP')
        class SpoofNear(FakeHost):
            class Spoof(ValueError):
                pass
            def schedule(self, required):
                raise self.Spoof('ADMISSION_TIMER_NEAR')
        class SpoofQuiescence(FakeHost):
            class Spoof(ValueError):
                pass
            def schedule(self, required):
                raise self.Spoof('ADMISSION_JOBS')
        class ExtraArgument(FakeHost):
            def schedule(self, required):
                raise ValueError('ADMISSION_JOBS', 'SECRET=/private/path')
        class SpoofedArgument(FakeHost):
            class Spoof(str):
                pass
            def schedule(self, required):
                raise ValueError(self.Spoof('ADMISSION_JOBS'))
        class SpoofArgNear(FakeHost):
            class SpoofArg:
                def __str__(self):
                    return 'ADMISSION_TIMER_NEAR'
                def __eq__(self, other):
                    return True
            def schedule(self, required):
                raise ValueError(self.SpoofArg())
        class Unknown(FakeHost):
            def schedule(self, required):
                raise RuntimeError('SECRET=/private/observation')
        cases = (
            (FakeHost(busy=1000),
             b'OP_SCHEDULE_TIMER_PROXIMITY_NO_AUTO_RETRY\n'),
            (ApiNear(), b'OP_SCHEDULE_UNKNOWN_NO_AUTO_RETRY\n'),
            (FakeHost(fail='schedule'),
             b'OP_SCHEDULE_QUIESCENCE_UNVERIFIED_NO_AUTO_RETRY\nOP_SCHEDULE_PREDICATE=ADMISSION_JOBS\n'),
            (ResourceGate(), b'OP_SCHEDULE_RESOURCE_GATE_NO_AUTO_RETRY\nOP_SCHEDULE_PREDICATE=ADMISSION_HEADROOM\n'),
            (Activity(), b'OP_SCHEDULE_ACTIVITY_OBSERVED_NO_AUTO_RETRY\nOP_SCHEDULE_PREDICATE=ADMISSION_WORKER_DESCENDANT\n'),
            (AmbiguousTopology(), b'OP_SCHEDULE_UNKNOWN_NO_AUTO_RETRY\n'),
            (SpoofNear(), b'OP_SCHEDULE_UNKNOWN_NO_AUTO_RETRY\n'),
            (SpoofQuiescence(), b'OP_SCHEDULE_UNKNOWN_NO_AUTO_RETRY\n'),
            (ExtraArgument(), b'OP_SCHEDULE_UNKNOWN_NO_AUTO_RETRY\n'),
            (SpoofedArgument(), b'OP_SCHEDULE_UNKNOWN_NO_AUTO_RETRY\n'),
            (SpoofArgNear(), b'OP_SCHEDULE_UNKNOWN_NO_AUTO_RETRY\n'),
            (Unknown(), b'OP_SCHEDULE_UNKNOWN_NO_AUTO_RETRY\n'),
        )
        for host, marker in cases:
            with self.subTest(marker=marker):
                written = []
                with patch.object(op.os, 'write', side_effect=lambda fd, raw:
                                  written.append((fd, raw)) or len(raw)):
                    with self.assertRaises((ValueError, RuntimeError)):
                        self.run_case(host)
                self.assertEqual(written, [(2, marker)])
                self.assertEqual(self.names(), set())
                self.assertEqual(host.spawns, 0)

    def test_every_quiescence_code_is_bounded_before_audit(self):
        for code in sorted(op.SCHEDULE_QUIESCENCE_CODES):
            class Rejected(FakeHost):
                def schedule(self, required):
                    raise ValueError(code)
            written = []
            with self.subTest(code=code):
                with patch.object(op.os, 'write', side_effect=lambda fd, raw:
                                  written.append((fd, raw)) or len(raw)):
                    with self.assertRaisesRegex(ValueError, code):
                        self.run_case(Rejected())
                self.assertEqual(written, [(2,
                    b'OP_SCHEDULE_QUIESCENCE_UNVERIFIED_NO_AUTO_RETRY\n'
                    b'OP_SCHEDULE_PREDICATE=' + code.encode('ascii') + b'\n')])
                self.assertEqual(self.names(), set())

    def test_failed_schedule_marker_write_still_blocks_without_spawn(self):
        host = FakeHost(fail='schedule')
        with patch.object(op.os, 'write', side_effect=OSError('synthetic full stderr')):
            with self.assertRaisesRegex(ValueError, 'ADMISSION_JOBS'):
                self.run_case(host)
        self.assertEqual(self.names(), set())
        self.assertEqual(host.spawns, 0)

    def test_schedule_classifier_rejects_spoof_and_ambiguous_codes(self):
        class Spoof(ValueError):
            pass
        self.assertEqual(op.schedule_reason(ValueError('ADMISSION_JOBS')),
                         'QUIESCENCE_UNVERIFIED')
        self.assertEqual(op.schedule_reason(ValueError('ADMISSION_WORKER_CGROUP')),
                         'UNKNOWN')
        self.assertEqual(op.schedule_reason(ValueError('ADMISSION_CONTROLLER_BUSY')),
                         'ACTIVITY_OBSERVED')
        self.assertEqual(op.schedule_reason(Spoof('ADMISSION_JOBS')),
                         'UNKNOWN')
        self.assertEqual(op.schedule_reason(ValueError('ADMISSION_API_STATE')),
                         'UNKNOWN')

    def test_preflight_failure_audited_no_spawn(self):
        host = FakeHost(fail='preflight')
        with self.assertRaisesRegex(ValueError, 'OP_PREFLIGHT_BLOCK'):
            self.run_case(host)
        self.assertEqual(host.spawns, 0)
        self.assertEqual(self.names(), {'00_INTENT.json',
                                        '01_EARLY.json',
                                        '99_BLOCKED_OR_UNKNOWN.json'})
        report = json.loads((self.audit/'99_BLOCKED_OR_UNKNOWN.json').read_text())
        self.assertEqual((report['stage'], report['failure_code']),
                         ('SOURCE_PREFLIGHT', 'OP_PREFLIGHT_BLOCK'))

    def test_prepared_failure_audited_no_spawn(self):
        host = FakeHost(fail='prepared')
        with self.assertRaisesRegex(ValueError, 'LAUNCH_MANIFEST_CONTRACT'):
            self.run_case(host)
        self.assertEqual(host.spawns, 0)
        self.assertEqual(self.names(), {'00_INTENT.json',
                                        '99_BLOCKED_OR_UNKNOWN.json'})
        report = json.loads((self.audit/'99_BLOCKED_OR_UNKNOWN.json').read_text())
        self.assertEqual((report['stage'], report['failure_code']),
                         ('PREPARED_FIRST', 'LAUNCH_MANIFEST_CONTRACT'))

    def test_every_early_check_records_attempted_predicate_without_spawn(self):
        codes = {
            'HEADROOM': 'OP_HEADROOM',
            'CONTROLLERS': 'OP_FILE_META',
            'LOCK_METADATA': 'OP_LOCK_PIN',
            'SCOPE_CGROUP': 'OP_SCOPE_CGROUP_PRESENT',
            'SCOPE_UNIT': 'OP_SCOPE_PRESENT',
            'TOOL_PRELOAD': 'OP_TOOL_MODULE_PRELOADED',
            'BACKING_SOURCE': 'OP_FILE_CHANGED',
            'BACKING_IMPORT': 'OS_ERROR',
            'BACKING_MODULES': 'OP_TOOL_MODULE_PATH',
            'BACKING_SNAPSHOT': 'OS_ERROR',
            'BACKING_COMPARE': 'OP_BACKING_CHANGED',
        }
        self.assertEqual(set(codes), op.EARLY_CHECKS)
        for check, code in codes.items():
            with self.subTest(check=check):
                class EarlyFail(FakeHost):
                    def early(self, item):
                        self.early_check = check
                        if code == 'OS_ERROR':
                            raise OSError('secret path and environment')
                        raise ValueError(code)
                host = EarlyFail()
                with self.assertRaises((ValueError, OSError)):
                    self.run_case(host)
                report = json.loads((self.audit/'99_BLOCKED_OR_UNKNOWN.json').read_text())
                self.assertEqual((report['stage'], report['attempted_check'],
                                  report['failure_code']),
                                 ('EARLY_FIRST', check, code))
                self.assertEqual(host.spawns, 0)
                self.assertNotIn('secret', json.dumps(report))
                for child in self.audit.iterdir():
                    child.unlink()
                self.audit.rmdir()

    def test_final_early_failure_is_attributed_to_final_pass(self):
        class FinalFail(FakeHost):
            def __init__(self):
                super().__init__()
                self.count = 0
            def early(self, item):
                self.count += 1
                if self.count == 2:
                    self.early_check = 'LOCK_METADATA'
                    raise ValueError('OP_LOCK_CHANGED')
                return super().early(item)
        host = FinalFail()
        with self.assertRaisesRegex(ValueError, 'OP_LOCK_CHANGED'):
            self.run_case(host)
        report = json.loads((self.audit/'99_BLOCKED_OR_UNKNOWN.json').read_text())
        self.assertEqual((report['stage'], report['attempted_check'],
                          report['failure_code']),
                         ('EARLY_FINAL', 'LOCK_METADATA', 'OP_LOCK_CHANGED'))
        self.assertEqual(host.spawns, 0)

    def test_unknown_exception_cannot_put_sensitive_text_in_audit(self):
        secret = 'SECRET=/private/credentials token abcdef123456'
        class Unknown(FakeHost):
            def early(self, item):
                self.early_check = 'CONTROLLERS'
                raise RuntimeError(secret)
        host = Unknown()
        with self.assertRaises(RuntimeError):
            self.run_case(host)
        raw = (self.audit/'99_BLOCKED_OR_UNKNOWN.json').read_text()
        report = json.loads(raw)
        self.assertEqual((report['stage'], report['attempted_check'],
                          report['failure_code']),
                         ('EARLY_FIRST', 'CONTROLLERS', 'UNKNOWN'))
        self.assertNotIn(secret, raw)
        self.assertEqual(report['attempt_id'],self.approved['attempt_id'])
        self.assertEqual(report['cleanup_state'],'UNVERIFIED')
        self.assertEqual(host.spawns, 0)

    def test_spoofed_valueerror_and_check_are_not_recorded(self):
        class Spoof(ValueError):
            pass
        report = op.failure_record('EARLY_FIRST',
            SimpleNamespace(early_check='SECRET_PATH'), Spoof('OP_HEADROOM'))
        self.assertEqual((report['stage'], report['attempted_check'],
                          report['failure_code']),
                         ('EARLY_FIRST', 'UNKNOWN', 'UNKNOWN'))

    def test_missing_false_or_ambiguous_early_observation_blocks(self):
        for reason in ('early', 'false_early'):
            with self.subTest(reason=reason):
                if self.audit.exists():
                    self.fail('tests must use a fresh isolated audit')
                host = FakeHost(fail=reason)
                with self.assertRaisesRegex(ValueError,
                        'OP_BACKING_CHANGED|OP_EARLY_RESULT'):
                    self.run_case(host)
                self.assertEqual(host.spawns, 0)
                self.assertEqual(self.names(), {'00_INTENT.json',
                                                '99_BLOCKED_OR_UNKNOWN.json'})
                # Remove only this disposable fixture between subtests.
                for child in self.audit.iterdir():
                    child.unlink()
                self.audit.rmdir()

    def test_final_early_requires_complete_exact_schema(self):
        for missing in ({}, {'headroom': 'PASS'}):
            with self.subTest(missing=missing):
                class Incomplete(FakeHost):
                    def __init__(self):
                        super().__init__()
                        self.early_calls=0
                    def early(self,item):
                        self.early_calls+=1
                        if self.early_calls == 2:
                            return missing
                        return super().early(item)
                host=Incomplete()
                with self.assertRaisesRegex(ValueError,'OP_EARLY_RESULT'):
                    self.run_case(host)
                self.assertEqual(host.spawns,0)
                self.assertIn('01_EARLY.json',self.names())
                self.assertNotIn('02_FINAL_PRECHECK.json',self.names())
                self.assertIn('99_BLOCKED_OR_UNKNOWN.json',self.names())
                for child in self.audit.iterdir():
                    child.unlink()
                self.audit.rmdir()

    def test_real_early_adapter_rejects_topology_and_scope(self):
        host = object.__new__(op.Host)
        host.tool_modules = None
        wanted = {'origins': {'8:1': '8:0'}, 'origin_nodes': {'8:0': '/dev/sda'},
                  'paths': [['/one', '8:1', '8:0']]}
        reader = Mock()
        reader.headroom.return_value = True
        reader.missing.return_value = True
        host.admission = Mock(HostReader=Mock(return_value=reader),
                             COOPERATIVE_CONTROLLERS={'/x': 'a'*64})
        host.launcher = Mock(STAGE='/stage', TOOL='/tool', UNIT='unit.scope',
                             ORIGIN='8:0', TOOL_PINS={'operator_entry.py': 'a'*64})
        entry = Mock()
        entry._backing_paths.return_value = ('/one',)
        entry._verified_backing.return_value = wanted
        props = {'LoadState': 'not-found', 'ActiveState': 'inactive'}
        with patch.object(op, 'read_pinned', return_value=b'exact'), \
                patch.object(op, 'load_pinned', return_value=entry), \
                patch.object(op, 'lock_metadata', return_value=True), \
                patch.object(op, 'TOOL_MODULES', ()), \
                patch.object(host.admission, '_property', return_value=props):
            result = host.early({'source_to_origin': wanted})
            self.assertTrue(all(value == 'PASS' for value in result.values()))
            host.admission._property.assert_called_with(
                reader, 'unit.scope', ('LoadState', 'ActiveState'))
            entry._verified_backing.return_value = {'origins': {'8:1': '9:0'},
                'origin_nodes': {'9:0': '/dev/other'}, 'paths': []}
            with self.assertRaisesRegex(ValueError, 'OP_BACKING_CHANGED'):
                host.early({'source_to_origin': wanted})
            self.assertEqual(host.early_check, 'BACKING_COMPARE')
            entry._verified_backing.return_value = wanted
            reader.missing.return_value = False
            with self.assertRaisesRegex(ValueError, 'OP_SCOPE_CGROUP_PRESENT'):
                host.early({'source_to_origin': wanted})
            self.assertEqual(host.early_check, 'SCOPE_CGROUP')
            reader.missing.return_value = True
            entry._verified_backing.side_effect = ValueError(
                'ENTRY_COMPLEX_BLOCK_DEVICE')
            with self.assertRaisesRegex(ValueError,
                                        'ENTRY_COMPLEX_BLOCK_DEVICE'):
                host.early({'source_to_origin': wanted})
            self.assertEqual(host.early_check, 'BACKING_SNAPSHOT')

    def test_pinned_operator_entry_import_shape(self):
        raw = fixture(_ENTRY_B85,
            'd7402cc58c1871294fedc85d3c484ed7ca5858ca598d78d8f409a5cf903cd173')
        # The entry source imports three runtime siblings. Stub only their
        # import names here; their actual protected bytes are checked live.
        imports = {name: SimpleNamespace() for name in
                   ('admission', 'capture_once', 'restricted_launcher')}
        with patch.dict(sys.modules, imports, clear=False), \
                patch.object(sys, 'path', list(sys.path)):
            module = op.load_pinned('zen53_synthetic_backing_import', raw,
                                    filename='/sealed/tool/operator_entry.py')
        self.assertTrue(callable(module._backing_paths))
        self.assertTrue(callable(module._verified_backing))

    def test_tool_module_provenance_rejects_preload_path_bytes_and_swap(self):
        tool='/sealed/tool'
        pins={name+'.py':'a'*64 for name in op.TOOL_MODULES}
        modules={name:SimpleNamespace(__file__=tool+'/'+name+'.py')
                 for name in op.TOOL_MODULES}
        with patch.dict(sys.modules,modules,clear=False), \
                patch.object(op,'read_pinned',return_value=b'pinned') as reader:
            saved=op.verified_tool_imports(tool,pins)
            self.assertEqual(len(saved),len(op.TOOL_MODULES))
            self.assertEqual(reader.call_count,len(op.TOOL_MODULES))
            name=op.TOOL_MODULES[0]
            sys.modules[name]=SimpleNamespace(__file__='/ambient/'+name+'.py')
            with self.assertRaisesRegex(ValueError,'OP_TOOL_MODULE_PATH'):
                op.verified_tool_imports(tool,pins)
            sys.modules[name]=SimpleNamespace(__file__=tool+'/'+name+'.py')
            with self.assertRaisesRegex(ValueError,'OP_TOOL_MODULE_SUBSTITUTED'):
                op.verified_tool_imports(tool,pins,previous=saved)
            sys.modules[name]=saved[name]
            with patch.object(op,'read_pinned',side_effect=ValueError(
                    'OP_FILE_CHANGED')):
                with self.assertRaisesRegex(ValueError,'OP_FILE_CHANGED'):
                    op.verified_tool_imports(tool,pins,previous=saved)

    def test_host_rejects_preloaded_tool_module_before_any_file_read(self):
        with patch.dict(sys.modules,{'admission':SimpleNamespace(
                __file__='/ambient/admission.py')},clear=False), \
                patch.object(op,'read_pinned') as reader:
            with self.assertRaisesRegex(ValueError,'OP_TOOL_MODULE_PRELOADED'):
                op.Host('/tool/admission.py','/preflight','/launcher', self.approved)
            reader.assert_not_called()

    def test_lock_metadata_rejects_owner_or_parent_drift(self):
        def st(mode,dev,ino,uid,gid,ctime=1,nlink=1):
            return SimpleNamespace(st_mode=mode,st_dev=dev,st_ino=ino,
                st_uid=uid,st_gid=gid,st_ctime_ns=ctime,st_nlink=nlink)
        parent=st(stat.S_IFDIR|0o700,1,2,1001,1001)
        root=st(stat.S_IFDIR|0o755,1,1,0,0)
        lock=st(stat.S_IFREG|0o600,1,3,1001,1001)
        states={'/':root,'/locks':parent,'/locks/a':lock}
        admission=Mock(LOCKS=(('/locks/a',1,3,1001,1001,0o600),),
                       COOPERATIVE_ANCESTORS={'/locks':(1,2,1001,1001,0o700)})
        def lookup(path): return states[path]
        closed=[]
        kw=dict(lstat=lookup,open_file=lambda *_args:9,
                fstat=lambda _fd:lock,close=closed.append)
        self.assertTrue(op.lock_metadata(admission,**kw))
        self.assertEqual(closed,[9])
        states['/locks']=st(stat.S_IFDIR|0o700,1,4,1001,1001)
        with self.assertRaisesRegex(ValueError,'OP_LOCK_ANCESTOR'):
            op.lock_metadata(admission,**kw)
        states['/locks']=parent
        states['/locks/a']=st(stat.S_IFREG|0o600,1,3,1002,1001)
        with self.assertRaisesRegex(ValueError,'OP_LOCK_PIN'):
            op.lock_metadata(admission,**kw)

    def test_audit_creation_fsync_failure_is_consumed(self):
        host=FakeHost()
        with patch.object(op.os,'fsync',side_effect=OSError('synthetic fsync')):
            with self.assertRaises(OSError):
                self.run_case(host)
        self.assertTrue(self.audit.is_dir())
        self.assertEqual(self.names(),set())
        self.assertEqual(host.spawns,0)
        with self.assertRaises(FileExistsError):
            self.run_case(host)

    def test_receipt_fsync_failure_consumes_without_spawn(self):
        host=FakeHost()
        real_fsync=op.os.fsync
        calls=[0]
        def fail_third(fd):
            calls[0]+=1
            if calls[0]==3:
                raise OSError('synthetic receipt fsync')
            return real_fsync(fd)
        with patch.object(op.os,'fsync',side_effect=fail_third):
            with self.assertRaisesRegex(OSError,'receipt fsync'):
                self.run_case(host)
        self.assertEqual(calls[0],3)
        self.assertIn('00_INTENT.json',self.names())
        self.assertEqual(host.spawns,0)

    def test_created_audit_named_identity_substitution_blocks(self):
        parent=os.open(self.root,op.DIR_FLAGS)
        real_stat=op.os.stat
        def swapped(name,*args,**kwargs):
            result=real_stat(name,*args,**kwargs)
            if name == self.audit.name and kwargs.get('dir_fd') == parent:
                values={k:getattr(result,k) for k in
                    ('st_mode','st_dev','st_ino','st_uid','st_gid')}
                values['st_ino']+=1
                return SimpleNamespace(**values)
            return result
        try:
            with patch.object(op.os,'stat',side_effect=swapped):
                with self.assertRaisesRegex(ValueError,'OP_AUDIT_NEW_META'):
                    op._create_audit_at(parent,self.audit.name,
                        owner_uid=os.geteuid(),owner_gid=os.getegid())
        finally:
            os.close(parent)
        self.assertTrue(self.audit.is_dir())
        self.assertEqual(self.names(),set())

    def test_existing_v1_object_is_untouched(self):
        old=self.root / ('zen53-operator-v1-' + op.NONCE)
        old.mkdir(mode=0o700)
        (old/'historical').write_bytes(b'keep')
        self.run_case(FakeHost())
        self.assertEqual((old/'historical').read_bytes(),b'keep')

    def test_first_receipt_write_failure_never_spawns(self):
        host=FakeHost()
        with patch.object(op.os,'write',side_effect=OSError('synthetic write')):
            with self.assertRaises(OSError):
                self.run_case(host)
        self.assertIn('00_INTENT.json',self.names())
        self.assertEqual(host.spawns,0)

    def test_intent_audit_failure_emits_only_fixed_fallback(self):
        host = FakeHost()
        written = []
        def fail_intent(fd, name, payload):
            raise OSError('SECRET=/private/intended-record')
        with patch.object(op, 'audit_record', side_effect=fail_intent), \
                patch.object(op.os, 'write', side_effect=lambda fd, raw:
                             written.append((fd, raw)) or len(raw)):
            with self.assertRaises(OSError):
                self.run_case(host)
        self.assertEqual(written, [(2, b'OP_AUDIT_INTENT_FAILED_NO_RETRY\n')])
        self.assertEqual(self.names(), set())
        self.assertEqual(host.spawns, 0)

    def test_later_receipt_failure_retains_unknown(self):
        host=FakeHost()
        original=op.audit_record
        def fail_after_first(fd,name,payload):
            if name=='01_EARLY.json':
                raise OSError('synthetic later receipt failure')
            return original(fd,name,payload)
        with patch.object(op,'audit_record',side_effect=fail_after_first):
            with self.assertRaises(OSError):
                self.run_case(host)
        self.assertEqual(self.names(),{'00_INTENT.json',
                                      '99_BLOCKED_OR_UNKNOWN.json'})
        self.assertEqual(host.spawns,0)

    def test_terminal_audit_write_failure_has_only_fixed_stderr_fallback(self):
        host = FakeHost(fail='early')
        original = op.audit_record
        real_write = op.os.write
        written = []
        def fail_terminal(fd, name, payload):
            if name == '99_BLOCKED_OR_UNKNOWN.json':
                raise OSError('SECRET=/private/audit')
            return original(fd, name, payload)
        def capture_fallback(fd, raw):
            if fd == 2:
                written.append((fd, raw))
                return len(raw)
            return real_write(fd, raw)
        with patch.object(op, 'audit_record', side_effect=fail_terminal), \
                patch.object(op.os, 'write', side_effect=capture_fallback):
            with self.assertRaisesRegex(ValueError, 'OP_BACKING_CHANGED'):
                self.run_case(host)
        self.assertEqual(written, [(2, b'OP_AUDIT_TERMINAL_FAILED_NO_RETRY\n')])
        self.assertEqual(self.names(), {'00_INTENT.json'})
        self.assertEqual(host.spawns, 0)

    def test_last_schedule_change_fail_closed(self):
        class Changing(FakeHost):
            def schedule(self, required):
                super().schedule(required)
                if sum(e[0] == 'schedule' for e in self.events) == 2:
                    raise ValueError('ADMISSION_TIMER_NEAR')
        host = Changing()
        with self.assertRaisesRegex(ValueError, 'ADMISSION_TIMER_NEAR'):
            self.run_case(host)
        self.assertEqual(host.spawns, 0)
        self.assertNotIn('03_HANDOFF.json', self.names())
        report = json.loads((self.audit/'99_BLOCKED_OR_UNKNOWN.json').read_text())
        self.assertEqual((report['stage'], report['failure_code']),
                         ('FINAL_SCHEDULE', 'ADMISSION_TIMER_NEAR'))

    def test_final_schedule_codes_retain_exact_safe_predicate(self):
        for code in ('ADMISSION_CONTROLLER_BUSY', 'ADMISSION_JOBS',
                     'ADMISSION_HEADROOM', 'ADMISSION_WORKER_CGROUP'):
            with self.subTest(code=code):
                class FinalCode(FakeHost):
                    def __init__(self):
                        super().__init__()
                        self.calls = 0
                    def schedule(self, required):
                        self.calls += 1
                        if self.calls == 2:
                            raise ValueError(code)
                host = FinalCode()
                with self.assertRaisesRegex(ValueError, code):
                    self.run_case(host)
                report = json.loads((self.audit/'99_BLOCKED_OR_UNKNOWN.json').read_text())
                self.assertEqual((report['stage'], report['failure_code']),
                                 ('FINAL_SCHEDULE', code))
                self.assertEqual(host.spawns, 0)
                for child in self.audit.iterdir():
                    child.unlink()
                self.audit.rmdir()

    def test_preflight_exhausts_total_launch_deadline(self):
        timer = Clock()
        class Slow(FakeHost):
            def preflight(self, package, recovery, archive):
                super().preflight(package, recovery, archive)
                timer.now += op.WAIT_LIMIT + 1
        host = Slow()
        with self.assertRaisesRegex(ValueError, 'OP_SCHEDULE_DEADLINE'):
            self.run_case(host, timer)
        self.assertEqual(host.spawns, 0)
        self.assertNotIn('03_HANDOFF.json', self.names())
        self.assertIn('99_BLOCKED_OR_UNKNOWN.json', self.names())

    def test_launcher_pre_spawn_work_exhausts_deadline(self):
        timer = Clock()
        class SlowLauncher(FakeHost):
            def launch(self, archive, package, before_spawn):
                timer.now += op.WAIT_LIMIT + 1
                return super().launch(archive, package, before_spawn)
        host = SlowLauncher()
        with self.assertRaisesRegex(ValueError, 'OP_SCHEDULE_DEADLINE'):
            self.run_case(host, timer)
        self.assertEqual(host.spawns, 0)
        self.assertIn('03_HANDOFF.json', self.names())
        self.assertIn('99_BLOCKED_OR_UNKNOWN.json', self.names())

    def test_uncertain_spawn_is_durable_and_never_retried(self):
        host = FakeHost(fail='spawn')
        with self.assertRaises(OSError):
            self.run_case(host)
        self.assertEqual(host.spawns, 1)
        self.assertIn('99_BLOCKED_OR_UNKNOWN.json', self.names())
        self.assertNotIn('04_RESULT.json', self.names())
        with self.assertRaises(FileExistsError):
            self.run_case(host)
        self.assertEqual(host.spawns, 1)

    def test_existing_object_collision_never_overwrites(self):
        self.audit.mkdir(mode=0o700)
        existing = self.audit / '00_INTENT.json'
        existing.write_bytes(b'prior evidence')
        host = FakeHost()
        with self.assertRaises(FileExistsError):
            self.run_case(host)
        self.assertEqual(existing.read_bytes(), b'prior evidence')
        self.assertEqual(host.spawns, 0)

    def test_symlink_audit_collision_never_follows_target(self):
        self.audit.symlink_to(self.root, target_is_directory=True)
        host=FakeHost()
        with self.assertRaises(FileExistsError):
            self.run_case(host)
        self.assertEqual(host.spawns,0)
        self.assertEqual(self.stage.read_bytes(),b'unchanged sealed stage')

    def test_audit_exclusive_create_rejects_existing_and_symlink(self):
        self.audit.mkdir(mode=0o700)
        fd = os.open(self.audit, op.DIR_FLAGS)
        try:
            op.audit_record(fd, 'a.json', {'ok': 1})
            with self.assertRaises(FileExistsError):
                op.audit_record(fd, 'a.json', {'ok': 2})
            (self.audit / 'link.json').symlink_to(self.stage)
            with self.assertRaises(FileExistsError):
                op.audit_record(fd, 'link.json', {'ok': 3})
            self.assertEqual((self.audit / 'a.json').read_bytes(),
                             op.canonical({'ok': 1}))
        finally:
            os.close(fd)

    def test_claim_failure_and_final_approval_change_never_spawn(self):
        for claim, recheck, expected, stage in (
            (lambda: False, lambda: True, 'OP_APPROVAL_CLAIM',
             'APPROVAL_CLAIM'),
            (lambda: True, lambda: False, 'OP_APPROVAL_CHANGED',
             'LAUNCH')):
            with self.subTest(expected=expected):
                host=FakeHost()
                with self.assertRaisesRegex(ValueError,expected):
                    op.run(host,package='/fake/package',
                           recovery='/fake/recovery',archive='/fake/archive',
                           audit_path=str(self.audit),approval_claim=claim,
                           approval_recheck=recheck,clock=Clock().clock,
                           sleep=lambda _:None)
                self.assertEqual(host.spawns,0)
                result=json.loads((self.audit/'99_BLOCKED_OR_UNKNOWN.json').read_bytes())
                self.assertEqual(result['stage'],stage)
                self.assertEqual(result['failure_code'],expected)
                # Each attempt must have a new audit nonce/location.
                for child in self.audit.iterdir():
                    child.unlink()
                self.audit.rmdir()

    def test_main_bad_args_cannot_spawn(self):
        self.assertEqual(op.main(['--bad']), 2)

    def test_main_valid_argv_wires_one_injected_handoff(self):
        values = ['/var/lib/zen53-owner-tool-' + op.NONCE + '/admission.py',
                  '/fixed/preflight.py', '/fixed/launch_once.py',
                  '/fixed/package', '/fixed/recovery', '/fixed/archive',
                  op.AUDIT]
        names = ['--admission', '--preflight', '--launcher', '--package',
                 '--recovery', '--archive', '--audit']
        argv = [item for pair in zip(names, values) for item in pair]
        with patch.object(op.os, 'geteuid', return_value=0), \
                patch.object(op.os, 'getegid', return_value=0), \
                patch.object(op, '__file__', op.OPERATOR_ROOT + '/operator_once.py'), \
                patch.object(op, 'APPROVAL_ROOT', '/trusted/approval'), \
                patch.object(op, 'read_pinned', return_value=b'pinned'), \
                patch.object(op, 'installed_config', return_value={
                    'approval_root':'/trusted/approval'}), \
                patch.object(op, 'load_pinned', side_effect=[
                    SimpleNamespace(decode=lambda raw,now: self.approved,
                                    BindingRejected=ValueError),
                    SimpleNamespace(configure=lambda contract: None,
                        RootStore=lambda root: SimpleNamespace(
                            read_current=lambda: b'pinned'))]), \
                patch.object(op, 'Host', return_value=object()) as host, \
                patch.object(op, 'run', return_value='RETURNED_ZERO_NOT_ACCEPTED') as run:
            self.assertEqual(op.main(argv), 1)
        host.assert_called_once_with(*values[:3], self.approved)
        self.assertEqual(run.call_args.kwargs['package'], values[3])
        self.assertEqual(run.call_args.kwargs['recovery'], values[4])
        self.assertEqual(run.call_args.kwargs['archive'], values[5])
        self.assertEqual(run.call_args.kwargs['audit_path'], values[6])

    def test_host_preflight_uses_exact_clean_command(self):
        host = object.__new__(op.Host)
        host.preflight_path = '/trusted/preflight.py'
        host.preflight_bytes = b'pinned'
        with patch.object(op, 'read_pinned', return_value=b'pinned'), \
                patch.object(op, 'bounded_command', return_value=(
                    0, b'PREFLIGHT_PASS routes=55 origins=8:0\n', b'')) as call:
            host.preflight('/p', '/r', '/a')
        argv = call.call_args.args[0]
        self.assertEqual(argv[:5], [op.PYTHON, '-I', '-S', '-B',
                                    '/trusted/preflight.py'])
        self.assertEqual(argv[5:], ['/p', '/r', '/a',
                                    op.PACKAGE_INVENTORY_SHA,
                                    op.PACKAGE_ARCHIVE_SHA])

    def test_host_preflight_rejects_wrong_result_and_source_change(self):
        host = object.__new__(op.Host)
        host.preflight_path = '/trusted/preflight.py'
        host.preflight_bytes = b'pinned'
        cases = [(1, b'PREFLIGHT_PASS routes=55 origins=8:0\n'),
                 (0, b'PREFLIGHT_PASS routes=54 origins=8:0\n'),
                 (0, b'X' * 4097)]
        for code, output in cases:
            with self.subTest(code=code, output=len(output)), \
                    patch.object(op, 'bounded_command', return_value=(
                        code, output, b'')):
                with self.assertRaisesRegex(ValueError, 'OP_PREFLIGHT_BLOCK'):
                    host.preflight('/p', '/r', '/a')
        with patch.object(op, 'bounded_command', return_value=(
                0, b'PREFLIGHT_PASS routes=55 origins=8:0\n', b'')), \
                patch.object(op, 'read_pinned', return_value=b'changed'):
            with self.assertRaisesRegex(ValueError, 'OP_PREFLIGHT_CHANGED'):
                host.preflight('/p', '/r', '/a')

    def test_real_adapter_enforces_deadline_at_spawn_callback(self):
        host = object.__new__(op.Host)
        def fake_launcher(*args, **kwargs):
            self.assertEqual(args, (op.SYSTEMD_SHA, '/a', '/p'))
            return kwargs['spawn'](['/bin/false'], shell=False)
        host.launcher = Mock(launch_once=fake_launcher)
        with patch.object(op.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(ValueError, 'OP_SCHEDULE_DEADLINE'):
                host.launch('/a', '/p', lambda: (_ for _ in ()).throw(
                    ValueError('OP_SCHEDULE_DEADLINE')))
            popen.assert_not_called()
            host.launch('/a', '/p', lambda: None)
            popen.assert_called_once_with(['/bin/false'], shell=False)

    def test_bounded_subprocess_output_and_clean_environment(self):
        script = ('import os,sys;'
                  'assert os.getenv("PYTHONPATH") is None;'
                  'sys.stdout.write("x"*4097)')
        with self.assertRaisesRegex(ValueError, 'OP_PREFLIGHT_OUTPUT_BOUND'):
            op.bounded_command([sys.executable, '-I', '-S', '-B', '-c', script],
                               deadline=5)
        code, out, err = op.bounded_command(
            [sys.executable, '-I', '-S', '-B', '-c',
             'import sys;sys.stdout.write("ok")'], deadline=5)
        self.assertEqual((code, out, err), (0, b'ok', b''))

    def test_bounded_subprocess_deadline(self):
        with self.assertRaisesRegex(ValueError, 'OP_PREFLIGHT_TIMEOUT'):
            op.bounded_command([sys.executable, '-I', '-S', '-B', '-c',
                                'import time;time.sleep(10)'], deadline=0.05)


if __name__ == '__main__':
    unittest.main()
