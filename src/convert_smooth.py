#! /usr/bin/env python
# -*- coding: utf-8 -*-
# vim:fenc=utf-8
import re
import logging
import traceback
import argparse
import os.path
import sys
import copy
from datetime import datetime
from PyQt5.QtGui import QQuaternion, QVector3D, QVector2D, QMatrix4x4, QVector4D
import math

from VmdWriter import VmdWriter, VmdBoneFrame
from VmdReader import VmdReader
from PmxModel import PmxModel, SizingException
from PmxReader import PmxReader
import wrapperutils, sub_arm_ik, utils, sub_arm_stance

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

is_print1 = False

def main(vmd_path, pmx_path, smooth_cnt, is_comp_circle, is_seam_smooth):

    try:
        # VMD読み込み
        motion = VmdReader().read_vmd_file(vmd_path)
        smoothed_frames = []

        # PMX読み込み
        model = PmxReader().read_pmx_file(pmx_path)

        if len(motion.frames.values()) > 0:
            smooth_vmd_fpath = re.sub(r'\.vmd$', "_smooth_{0:%Y%m%d_%H%M%S}.vmd".format(datetime.now()), vmd_path)
            
            # まずボーン回転の分散
            sub_arm_stance.spread_rotation(motion, copy.deepcopy(motion.frames), model)

            print("■■ スムージング -----------------")

            all_frames_by_bone = {}
            for bname in motion.frames.keys():
                frames_by_bone = {}
                for e, bf in enumerate(motion.frames[bname]):
                    frames_by_bone[bf.frame] = bf
                all_frames_by_bone[bname] = frames_by_bone

            for (bone_idx, bone_name) in model.bone_indexes.items():
                # 捩りとかの順番を担保するためモデルのINDEX順にそってスムージング

                if bone_name in all_frames_by_bone and len(all_frames_by_bone[bone_name]) > 2:
                    frames_by_bone = all_frames_by_bone[bone_name]

                    start_frameno = min(frames_by_bone.keys())
                    last_frameno = max(frames_by_bone.keys())

                    # 軸（ボーンの向き）を取得する
                    axis = QVector3D()
                    if bone_name in model.bones:
                        axis = utils.calc_local_axis(model, bone_name)

                    for cnt in range(smooth_cnt):
                        if cnt == 0:
                            utils.output_message("cnt==0 split_bf開始 %s" % (bone_name), is_print1)

                            prev_bf = calc_bone_by_complement_by_bone(frames_by_bone, bone_name, start_frameno, is_only=True, is_exist=True, is_key=True)
                            if not prev_bf: break

                            now_bf = calc_bone_by_complement_by_bone(frames_by_bone, bone_name, prev_bf.frame + 1, is_only=False, is_exist=True, is_key=True)
                            if not now_bf: break

                            next_bf = calc_bone_by_complement_by_bone(frames_by_bone, bone_name, now_bf.frame + 1, is_only=False, is_exist=True, is_key=True)
                            if not next_bf: break

                            prev_prev_bf = None

                            # 最初に根性打ち
                            while now_bf.frame <= last_frameno:
                                split_bf(all_frames_by_bone, frames_by_bone, model, bone_name, axis, prev_prev_bf, prev_bf, now_bf, next_bf, is_comp_circle, is_seam_smooth)
                                utils.output_message("cnt==0 split_bf %s %s" % (bone_name, now_bf.frame) , is_print1)

                                prev_prev_bf = prev_bf
                                
                                prev_bf = calc_bone_by_complement_by_bone(frames_by_bone, bone_name, now_bf.frame, is_only=True, is_exist=True, is_key=True)
                                if not prev_bf: break

                                now_bf = calc_bone_by_complement_by_bone(frames_by_bone, bone_name, prev_bf.frame + 1, is_only=False, is_exist=True, is_key=True)
                                if not now_bf: break

                                next_bf = calc_bone_by_complement_by_bone(frames_by_bone, bone_name, now_bf.frame + 1, is_only=False, is_exist=True, is_key=True)

                                if not next_bf:
                                    # 次がない場合、一旦prevの次に伸ばす
                                    next_bf = calc_bone_by_complement_by_bone(frames_by_bone, bone_name, now_bf.frame + 1, is_only=False, is_exist=False)
                                    next_bf.position += QVector3D(0.1, 0.1, 0.1)
                                    next_bf.rotation *= 1.1
                                    frames_by_bone[next_bf.frame] = next_bf
                                else:
                                    if next_bf.frame > last_frameno:
                                        # 次以降は見なくていいので終了
                                        break

                            utils.output_message("cnt==0 split_bf終了 %s" % (bone_name), is_print1)

                            # 円形補間の場合、全体をフィルタに掛ける
                            if is_comp_circle:
                                for _ in range(2):
                                    smooth_filter(all_frames_by_bone, frames_by_bone, model, bone_name, {"freq": 30, "mincutoff": 0.01, "beta": 0.8, "dcutoff": 1})

                            utils.output_message("cnt==0 smooth_filter終了 %s" % (bone_name), is_print1)

                        if cnt > 0:
                            utils.output_message("cnt>0 smooth_filter開始 %s" % (bone_name), is_print1)

                            # 2回目以降はフィルタあり
                            smooth_filter(all_frames_by_bone, frames_by_bone, model, bone_name, {"freq": 30, "mincutoff": 0.01, "beta": 0.5, "dcutoff": 0.8})

                            utils.output_message("cnt>0 smooth_filter完了 %s" % (bone_name), is_print1)

                            prev_bf = calc_bone_by_complement_by_bone(frames_by_bone, bone_name, start_frameno, is_only=True, is_exist=True)
                            if not prev_bf: break

                            now_bf = calc_bone_by_complement_by_bone(frames_by_bone, bone_name, prev_bf.frame + 1, is_only=False, is_exist=True)
                            if not now_bf: break

                            next_bf = calc_bone_by_complement_by_bone(frames_by_bone, bone_name, now_bf.frame + 1, is_only=False, is_exist=True)
                            if not next_bf: break

                            # 2回目以降はベジェ曲線同士を結合する
                            while now_bf.frame <= last_frameno:
                            
                                next_bf = smooth_bf(all_frames_by_bone, frames_by_bone, model, bone_name, prev_bf, now_bf, next_bf, (cnt + 2), last_frameno)
                                # utils.output_message("cnt>0 smooth_bf %s %s" % (bone_name, now_bf.frame), False)
                                
                                if not now_bf or not next_bf: break

                                # 二回目以降はnextまで補間曲線が設定できたので次
                                prev_bf = calc_bone_by_complement_by_bone(frames_by_bone, bone_name, next_bf.frame, is_only=True, is_exist=True)
                                if not prev_bf: break

                                now_bf = calc_bone_by_complement_by_bone(frames_by_bone, bone_name, prev_bf.frame + 1, is_only=False, is_exist=True)
                                if not now_bf: break

                                next_bf = calc_bone_by_complement_by_bone(frames_by_bone, bone_name, now_bf.frame + 1, is_only=False, is_exist=True)
                                # if not next_bf: break

                                if not next_bf:
                                    # 次がない場合、一旦prevの次に伸ばす
                                    next_bf = calc_bone_by_complement_by_bone(frames_by_bone, bone_name, now_bf.frame + 1, is_only=False, is_exist=False)
                                    next_bf.position += QVector3D(1, 0, 0)
                                    next_bf.rotation *= 1.1
                                else:
                                    if next_bf.frame > last_frameno:
                                        # 次以降は見なくていいので終了
                                        break

                            utils.output_message("cnt>0 smooth_filter完了 %s" % (bone_name), is_print1)

                        print("ボーン: %s %s回目" % (bone_name, (cnt + 1)))

                    for bf in frames_by_bone.values():
                        if bf.key == True:
                            smoothed_frames.append(bf)

            morph_frames = []
            for k,v in motion.morphs.items():
                for mf in v:
                    morph_frames.append(mf)

            writer = VmdWriter()
            
            # ボーンモーション生成
            writer.write_vmd_file(smooth_vmd_fpath, "Smooth Vmd", smoothed_frames, morph_frames, [], [], [], motion.showiks)

            print("スムージングVMD出力成功: %s" % smooth_vmd_fpath)
        else:
            print("スムージング対象となるキーがないため、終了します。")

        if len(motion.cameras) > 0:
            smooth_vmd_fpath = re.sub(r'\.vmd$', "_camera_{0:%Y%m%d_%H%M%S}.csv".format(datetime.now()), vmd_path)

            print("未実装")

    except Exception as e:
        print("■■■■■■■■■■■■■■■■■")
        print("■　**ERROR**　")
        print("■　VMDスムージング処理が意図せぬエラーで終了しました。")
        print("■■■■■■■■■■■■■■■■■")
        
        print(traceback.format_exc())

        raise e

def split_bf(all_frames_by_bone, frames_by_bone, model, bone_name, axis, prev_prev_bf, prev_bf, now_bf, next_bf, is_comp_circle, is_seam_smooth):
    # 0回目の場合、根性打ち

    for fno in range(prev_bf.frame + 1, next_bf.frame):
        if fno == now_bf.frame:
            continue

        utils.output_message("** split_bf %s fno: %s" % (bone_name, fno) , is_print1)
    
        target_bf = VmdBoneFrame()
        target_bf.frame = fno
        target_bf.name = bone_name.encode('cp932').decode('shift_jis').encode('shift_jis')
        target_bf.format_name = bone_name
        target_bf.key = True
        frames_by_bone[target_bf.frame] = target_bf

        if is_comp_circle:
            # 現在の補間曲線ではなく、円形補間した場合の角度を設定する
            target_rot, rt = get_smooth_middle_rot(axis, prev_bf, now_bf, next_bf, target_bf)
            target_bf.rotation = target_rot
            
            # 現在の補間曲線ではなく、円形補間した場合の位置を設定する
            target_pos, mt = get_smooth_middle_pos(axis, prev_bf, now_bf, next_bf, target_bf)
            target_bf.position = target_pos
        else:
            if target_bf.frame < now_bf.frame:
                # 現在の補間曲線で全打ち
                target_rot = calc_bone_by_complement_rot(prev_bf, now_bf, target_bf)
                target_bf.rotation = target_rot
                
                # 現在の補間曲線で全打ち
                target_pos = calc_bone_by_complement_pos(prev_bf, now_bf, target_bf)
                target_bf.position = target_pos
            else:
                # 現在の補間曲線で全打ち
                target_rot = calc_bone_by_complement_rot(now_bf, next_bf, target_bf)
                target_bf.rotation = target_rot
                
                # 現在の補間曲線で全打ち
                target_pos = calc_bone_by_complement_pos(now_bf, next_bf, target_bf)
                target_bf.position = target_pos

    if prev_prev_bf and is_seam_smooth and model.bones[bone_name].fixed_axis == QVector3D():
        # 前々回があって、滑らかに繋いで、かつ軸固定ではない場合のみ、間のキーにフィルタをかける
        beforeno = int(prev_bf.frame - (prev_bf.frame - prev_prev_bf.frame) * 0.5)
        middleno = int(now_bf.frame - (now_bf.frame - prev_bf.frame) * 0.5)
        afterno = int(next_bf.frame - (next_bf.frame - now_bf.frame) * 0.5)

        for (bno, ano) in [(beforeno, middleno), (middleno, afterno)]:       
            filter_frames_by_bone = {}
            for fno in range(bno, ano):
                # 前々回との間を滑らかに繋ぐための配列を用意する
                filter_frames_by_bone[fno] = copy.deepcopy(frames_by_bone[fno])

            utils.output_message("** split_bf copy %s fno: %s" % (bone_name, fno) , is_print1)

            # 前々回との連結部分をフィルタに掛ける
            smooth_filter(all_frames_by_bone, filter_frames_by_bone, model, bone_name, {"freq": 30, "mincutoff": 0.001, "beta": 0.05, "dcutoff": 1})

            utils.output_message("** split_bf filter %s fno: %s" % (bone_name, fno) , is_print1)

            # 元に戻す
            for f in filter_frames_by_bone.values():
                frames_by_bone[f.frame] = f

        utils.output_message("** split_bf return %s fno: %s" % (bone_name, fno) , is_print1)

# bf同士をベジェ曲線で繋ぐ
def smooth_bf(all_frames_by_bone, frames_by_bone, model, bone_name, prev_bf, now_bf, next_bf, offset, last_frameno):

    is_smooth = True
    successed_next_bf = None

    while is_smooth:
    
        # ひとまず補間曲線で繋ぐ
        is_smooth = smooth_complement_bf(all_frames_by_bone, frames_by_bone, model, bone_name, prev_bf, now_bf, next_bf, offset)
    
        if is_smooth:
            for fno in range(prev_bf.frame + 1, next_bf.frame):
                frames_by_bone[fno].key = False

            # 補間曲線が繋げた場合、次へ
            successed_next_bf = copy.deepcopy(next_bf)

            next_bf = calc_bone_by_complement_by_bone(frames_by_bone, bone_name, next_bf.frame + 1, is_only=False, is_exist=True)
            if not next_bf:
                # 次が見つからなければ、最後のnextを登録して終了
                if successed_next_bf:
                    successed_next_bf.key = True
                    frames_by_bone[successed_next_bf.frame] = successed_next_bf
                break
            
            now_bf = calc_bone_by_complement_by_bone(frames_by_bone, bone_name, int(prev_bf.frame + ((next_bf.frame - prev_bf.frame) / 2)), is_only=False, is_exist=True)
            # now_bf = calc_bone_by_complement_by_bone(frames_by_bone, bone_name, next_bf.frame - 1, is_only=False, is_exist=True)
            if not now_bf or (now_bf and now_bf.frame == last_frameno): break
        else:
            # 補間曲線が繋げなかった場合、終了
            if successed_next_bf:
                successed_next_bf.key = True
                frames_by_bone[successed_next_bf.frame] = successed_next_bf
            else:
                # 一回も補間曲線が登録出来なかった場合、とりあえずnowを登録して次にいく
                now_bf.key = True
                frames_by_bone[now_bf.frame] = now_bf
                successed_next_bf = now_bf
            break

    return successed_next_bf


def smooth_complement_bf(all_frames_by_bone, frames_by_bone, model, bone_name, prev_bf, now_bf, next_bf, offset):

    is_smooth_rot_result = smooth_bezier(all_frames_by_bone, frames_by_bone, model, bone_name, prev_bf, now_bf, next_bf, get_smooth_bezier_y_rot, get_smooth_bezier_now_bf_rot, \
        utils.R_x1_idxs, utils.R_y1_idxs, utils.R_x2_idxs, utils.R_y2_idxs, offset, utils.calc_smooth_bezier_rot)
    is_smooth_pos_x_result = smooth_bezier(all_frames_by_bone, frames_by_bone, model, bone_name, prev_bf, now_bf, next_bf, get_smooth_bezier_y_pos_x, get_smooth_bezier_now_bf_pos_x, \
        utils.MX_x1_idxs, utils.MX_y1_idxs, utils.MX_x2_idxs, utils.MX_y2_idxs, offset, utils.calc_smooth_bezier_pos)
    is_smooth_pos_y_result = smooth_bezier(all_frames_by_bone, frames_by_bone, model, bone_name, prev_bf, now_bf, next_bf, get_smooth_bezier_y_pos_y, get_smooth_bezier_now_bf_pos_y, \
        utils.MY_x1_idxs, utils.MY_y1_idxs, utils.MY_x2_idxs, utils.MY_y2_idxs, offset, utils.calc_smooth_bezier_pos)
    is_smooth_pos_z_result = smooth_bezier(all_frames_by_bone, frames_by_bone, model, bone_name, prev_bf, now_bf, next_bf, get_smooth_bezier_y_pos_z, get_smooth_bezier_now_bf_pos_z, \
        utils.MZ_x1_idxs, utils.MZ_y1_idxs, utils.MZ_x2_idxs, utils.MZ_y2_idxs, offset, utils.calc_smooth_bezier_pos)

    is_smooth = is_smooth_rot_result == is_smooth_pos_x_result == is_smooth_pos_y_result == is_smooth_pos_z_result
    
    # is_join = False

    # if is_smooth:
    #     # 補間曲線同士を結合する
    #     is_join_rot_result = join_bezier(frames_by_bone, prev_bf, now_bf, next_bf, get_smooth_bezier_y_rot, \
    #         utils.R_x1_idxs, utils.R_y1_idxs, utils.R_x2_idxs, utils.R_y2_idxs, offset, utils.join_smooth_bezier)
    #     is_join_pos_x_result = join_bezier(frames_by_bone, prev_bf, now_bf, next_bf, get_smooth_bezier_y_pos_x, \
    #         utils.MX_x1_idxs, utils.MX_y1_idxs, utils.MX_x2_idxs, utils.MX_y2_idxs, offset, utils.join_smooth_bezier)
    #     is_join_pos_y_result = join_bezier(frames_by_bone, prev_bf, now_bf, next_bf, get_smooth_bezier_y_pos_y, \
    #         utils.MY_x1_idxs, utils.MY_y1_idxs, utils.MY_x2_idxs, utils.MY_y2_idxs, offset, utils.join_smooth_bezier)
    #     is_join_pos_z_result = join_bezier(frames_by_bone, prev_bf, now_bf, next_bf, get_smooth_bezier_y_pos_z, \
    #         utils.MZ_x1_idxs, utils.MZ_y1_idxs, utils.MZ_x2_idxs, utils.MZ_y2_idxs, offset, utils.join_smooth_bezier)

    #     is_join = is_join_rot_result == is_join_pos_x_result == is_join_pos_y_result == is_join_pos_z_result

    return is_smooth

# 滑らかに繋ぐベジェ曲線
def smooth_bezier(all_frames_by_bone, frames_by_bone, model, bone_name, prev_bf, now_bf, next_bf, get_smooth_bezier_y, get_smooth_bezier_now_bf, x1_idxs, y1_idxs, x2_idxs, y2_idxs, offset, calc_smooth_bezier):
    if not (prev_bf.frame < now_bf.frame < next_bf.frame):
        # フレーム範囲外はNG
        return False

    # prev - next の間でもっとも変化が大きいのをnowとする
    now_bf = get_smooth_bezier_now_bf(all_frames_by_bone, model, bone_name, prev_bf, next_bf)

    x1 = prev_bf.frame
    x2 = now_bf.frame
    x3 = next_bf.frame

    y1 = get_smooth_bezier_y(all_frames_by_bone, model, bone_name, prev_bf)
    y2 = get_smooth_bezier_y(all_frames_by_bone, model, bone_name, now_bf)
    y3 = get_smooth_bezier_y(all_frames_by_bone, model, bone_name, next_bf)

    is_smooth, result, bz = calc_smooth_bezier(x1, y1, x2, y2, x3, y3, offset)

    if not is_smooth:
        # ベジェ曲線計算対象外の場合、とりあえずTRUEで終了
        return True

    if result == True:
        # 中間の始点を、中bfに設定する
        next_bf.complement[x1_idxs[0]] = next_bf.complement[x1_idxs[1]] = \
            next_bf.complement[x1_idxs[2]] = next_bf.complement[x1_idxs[3]] = bz[1].x()
        next_bf.complement[y1_idxs[0]] = next_bf.complement[y1_idxs[1]] = \
            next_bf.complement[y1_idxs[2]] = next_bf.complement[y1_idxs[3]] = bz[1].y()

        # 中間の終点を、中bfに設定する
        next_bf.complement[x2_idxs[0]] = next_bf.complement[x2_idxs[1]] = \
            next_bf.complement[x2_idxs[2]] = next_bf.complement[x2_idxs[3]] = bz[2].x()
        next_bf.complement[y2_idxs[0]] = next_bf.complement[y2_idxs[1]] = \
            next_bf.complement[y2_idxs[2]] = next_bf.complement[y2_idxs[3]] = bz[2].y()

        return True

    return False

# prev - next の間でもっとも変化が大きいnowを取得する
def get_smooth_bezier_now_bf_rot(all_frames_by_bone, model, bone_name, prev_bf, next_bf):
    # prevからnextに移動するまでの中間値
    avg_qq = QQuaternion.slerp(get_smooth_bezier_y_rot(all_frames_by_bone, model, bone_name, prev_bf), \
        get_smooth_bezier_y_rot(all_frames_by_bone, model, bone_name, next_bf), 0.5)

    max_dot_diff = 1
    max_dot_fno = prev_bf.frame + 1

    for fno in range(prev_bf.frame + 1, next_bf.frame):
        if fno in all_frames_by_bone[bone_name]:
            test_qq = get_smooth_bezier_y_rot(all_frames_by_bone, model, bone_name, all_frames_by_bone[bone_name][fno])
            test_dot = QQuaternion.dotProduct(avg_qq, test_qq)
            
            if test_dot < max_dot_diff:
                # dotが小さい（差が大きい）場合、採用
                max_dot_diff = test_dot
                max_dot_fno = fno

    return all_frames_by_bone[bone_name][max_dot_fno]

def get_smooth_bezier_now_bf_pos_x(all_frames_by_bone, model, bone_name, prev_bf, next_bf):
    return get_smooth_bezier_now_bf_pos(all_frames_by_bone, model, bone_name, prev_bf, next_bf, get_smooth_bezier_y_pos_x)

def get_smooth_bezier_now_bf_pos_y(all_frames_by_bone, model, bone_name, prev_bf, next_bf):
    return get_smooth_bezier_now_bf_pos(all_frames_by_bone, model, bone_name, prev_bf, next_bf, get_smooth_bezier_y_pos_y)

def get_smooth_bezier_now_bf_pos_z(all_frames_by_bone, model, bone_name, prev_bf, next_bf):
    return get_smooth_bezier_now_bf_pos(all_frames_by_bone, model, bone_name, prev_bf, next_bf, get_smooth_bezier_y_pos_z)

def get_smooth_bezier_now_bf_pos(all_frames_by_bone, model, bone_name, prev_bf, next_bf, get_smooth_bezier_y_pos):
    avg_pos = (QVector3D(get_smooth_bezier_y_pos(all_frames_by_bone, model, bone_name, prev_bf), 0, 0) \
        + QVector3D(get_smooth_bezier_y_pos(all_frames_by_bone, model, bone_name, next_bf), 0, 0)) / 2

    max_dot_diff = 1
    max_dot_fno = prev_bf.frame + 1

    for fno in range(prev_bf.frame + 1, next_bf.frame):
        if fno in all_frames_by_bone[bone_name]:
            test_pos = QVector3D(get_smooth_bezier_y_pos(all_frames_by_bone, model, bone_name, all_frames_by_bone[bone_name][fno]), 0, 0)
            test_dot = QVector3D.dotProduct(avg_pos, test_pos)
            
            if test_dot < max_dot_diff:
                # dotが小さい（差が大きい）場合、採用
                max_dot_diff = test_dot
                max_dot_fno = fno

    return all_frames_by_bone[bone_name][max_dot_fno]

def get_smooth_bezier_y_rot(all_frames_by_bone, model, bone_name, bf):
    rot = bf.rotation

    if model.bones[bone_name].fixed_axis == QVector3D():
        # 自身が軸制限なしの場合、子の回転を加算する
        for cbone in model.bones.values():
            if cbone.parent_index == model.bones[bone_name].index:
                if cbone.fixed_axis != QVector3D() and cbone.name in all_frames_by_bone and bf.frame in all_frames_by_bone[cbone.name]:
                    rot = bf.rotation * all_frames_by_bone[cbone.name][bf.frame].rotation.inverted()
    else:
        # 自身が軸制限ありの場合、親の回転を含めて考慮する
        parent_bone = model.bones[model.bone_indexes[model.bones[bone_name].parent_index]]
        if parent_bone.name in all_frames_by_bone and bf.frame in all_frames_by_bone[parent_bone.name]:
            rot = all_frames_by_bone[parent_bone.name][bf.frame].rotation * bf.rotation

    return copy.deepcopy(rot)
    
    # if model.bones[bone_name].fixed_axis == QVector3D():
    #     # 軸制限なしの場合、そのまま返す
    #     return bf.rotation
    
    # # 軸制限ありの場合、X軸回転量に変換する
    # degree = math.degrees(2 * math.acos(min(1, max(-1, bf.rotation.scalar()))))
    # qq = QQuaternion.fromAxisAndAngle(QVector3D(1, 0, 0), degree)

    # return qq

def get_smooth_bezier_y_pos_x(all_frames_by_bone, model, bone_name, bf):
    return bf.position.x()

def get_smooth_bezier_y_pos_y(all_frames_by_bone, model, bone_name, bf):
    return bf.position.y()

def get_smooth_bezier_y_pos_z(all_frames_by_bone, model, bone_name, bf):
    return bf.position.z()

# 滑らかにした移動
def get_smooth_middle_pos(axis, prev_bf, now_bf, next_bf, target_bf):
    p = prev_bf.position
    w = now_bf.position
    n = next_bf.position

    if target_bf.frame < now_bf.frame:
        # nowより前の場合
        # 変化量
        t = (target_bf.frame - prev_bf.frame) / ( now_bf.frame - prev_bf.frame)

        # デフォルト値
        d = w + (w - p)

        out = get_smooth_middle_by_vec3(p, w, n, d, t, target_bf.frame)
    else:
        # nowより後の場合
        # 変化量
        t = (target_bf.frame - now_bf.frame) / ( next_bf.frame - now_bf.frame)

        # デフォルト値
        d = n + (n - w)

        out = get_smooth_middle_by_vec3(w, n, p, d, t, target_bf.frame)

    # 円周上の座標とデフォルト値の内積（差分）
    diff = 1 - abs(QVector3D.dotProduct(target_bf.position.normalized(), out.normalized()))

    # 計算結果と実際の変化量を返す
    return out, diff

# 滑らかにした回転
def get_smooth_middle_rot(axis, prev_bf, now_bf, next_bf, target_bf):

    # 親子関係がある場合、軸に合わせた回転を試す
    if axis != QVector3D():
        degreep = math.degrees(2 * math.acos(min(1, max(-1, prev_bf.rotation.scalar()))))
        degreew = math.degrees(2 * math.acos(min(1, max(-1, now_bf.rotation.scalar()))))
        degreen = math.degrees(2 * math.acos(min(1, max(-1, next_bf.rotation.scalar()))))

        # 軸がある場合、その方向に回す
        p_qq = QQuaternion.fromAxisAndAngle(axis, degreep)
        w_qq = QQuaternion.fromAxisAndAngle(axis, degreew)
        n_qq = QQuaternion.fromAxisAndAngle(axis, degreen)

        p = p_qq.toEulerAngles()
        w = w_qq.toEulerAngles()
        n = n_qq.toEulerAngles()
    else:
        p_qq = prev_bf.rotation
        w_qq = now_bf.rotation
        n_qq = next_bf.rotation

        # 軸がない場合、そのまま回転
        p = p_qq.toEulerAngles()
        w = w_qq.toEulerAngles()
        n = n_qq.toEulerAngles()

    if target_bf.frame < now_bf.frame:
        # 変化量
        t = (target_bf.frame - prev_bf.frame) / ( now_bf.frame - prev_bf.frame)

        # デフォルト値
        d_qq = QQuaternion.slerp(p_qq, w_qq, t)
        d = d_qq.toEulerAngles()

        out = get_smooth_middle_by_vec3(p, w, n, d, t, target_bf.frame)
    else:
        # 変化量
        t = (target_bf.frame - now_bf.frame) / ( next_bf.frame - now_bf.frame)

        # デフォルト値
        d_qq = QQuaternion.slerp(w_qq, n_qq, t)
        d = d_qq.toEulerAngles()

        out = get_smooth_middle_by_vec3(w, n, p, d, t, target_bf.frame)

    out_qq = QQuaternion.fromEulerAngles(out)

    if axis != QVector3D():
        # 回転を元に戻す
        if target_bf.frame < now_bf.frame:
            d2_qq = QQuaternion.slerp(prev_bf.rotation, now_bf.rotation, t)
        else:
            d2_qq = QQuaternion.slerp(now_bf.rotation, next_bf.rotation, t)

        result_qq = (d_qq.inverted() * out_qq * d2_qq)
    else:
        result_qq = out_qq

    # 現在の回転と角度の中間地点との差(離れているほど値を大きくする)
    dot_diff = 1 - abs(QQuaternion.dotProduct(result_qq, target_bf.rotation))

    return result_qq, dot_diff

# vec3での滑らかな変化
def get_smooth_middle_by_vec3(op, ow, on, d, t, f):
    # 念のためコピー
    p = copy.deepcopy(op)
    w = copy.deepcopy(ow)
    n = copy.deepcopy(on)

    # 半径は3点間の距離の最長の半分
    r = max(p.distanceToPoint(w), p.distanceToPoint(n),  w.distanceToPoint(n)) / 2

    if round(r, 1) == 0:
        # 半径が取れなかった場合、そもそもまったく移動がないので、線分移動
        return (p + n) * t

    if p == w or p == n or w == n:
        # 半径が0の場合か、どれか同じ値の場合、線対称な値を使用する
        n = d

    # 3点を通る球体の原点を求める
    c, radius = utils.calc_sphere_center(p, w, n, r)

    if round(radius, 1) == 0:
        # 半径が取れなかった場合、そもそもまったく移動がないので、線分移動
        return (p + n) * t

    # prev -> now の t分の回転量
    pn_qq = QQuaternion.rotationTo((p - c).normalized(), (c - c).normalized())
    pw_qq = QQuaternion.rotationTo((p - c).normalized(), (w - c).normalized())
    # 球形補間の移動量
    t_qq = QQuaternion.slerp(pn_qq, pw_qq, t)

    out = t_qq * (p - c) + c

    # 値の変化がない場合、上書き
    if p.x() == w.x() == n.x():
        out.setX(w.x())
    if p.y() == w.y() == n.y():
        out.setY(w.y())
    if p.z() == w.z() == n.z():
        out.setZ(w.z())

    # 有効な値に変換
    utils.set_effective_value_vec3(out)

    return out


# 補間曲線を考慮した指定フレーム番号の位置
# https://www55.atwiki.jp/kumiho_k/pages/15.html
# https://harigane.at.webry.info/201103/article_1.html
def calc_bone_by_complement_by_bone(frames_by_bone, bone_name, frameno, is_only, is_exist, is_key=False, is_read=False):
    fill_bf = VmdBoneFrame()
    fill_bf.name = bone_name.encode('cp932').decode('shift_jis').encode('shift_jis')
    fill_bf.format_name = bone_name
    fill_bf.frame = frameno

    now_framenos = [x for x in sorted(frames_by_bone.keys()) if x == frameno]
    
    if len(now_framenos) == 1:
        if is_read:
            if frames_by_bone[frameno].read == True:
                return frames_by_bone[frameno]
            else:
                pass
        else:
            # キー指定がある場合、キーが有効である場合のみ返す
            if is_key:
                if frames_by_bone[frameno].key == True:
                    return frames_by_bone[frameno]
                else:
                    pass
            else:
                # 指定フレームがある場合、それを返す
                if is_exist:
                    # 存在しているものの場合、コピーしないでそのもの
                    return frames_by_bone[frameno]
                else:
                    return copy.deepcopy(frames_by_bone[frameno])
    elif is_only and is_exist:
        # 指定フレームがなく、かつそれ固定指定で、既存の場合、None
        return None

    after_framenos = [x for x in sorted(frames_by_bone.keys()) if x > frameno]
    
    if len(after_framenos) == 0:
        if is_exist == True:
            # 存在固定で、最後までいっても見つからなければ、None
            return None
        elif is_only == True:
            # 最後まで行っても見つからなければ、最終項目を該当フレーム用に設定して返す
            last_frameno = [x for x in sorted(frames_by_bone.keys())][-1]
            fill_bf = copy.deepcopy(frames_by_bone[last_frameno])
            return fill_bf

    if is_read == True:
        # キーONの場合、有効なのを返す
        for af in after_framenos:
            if frames_by_bone[af].read == True:
                return frames_by_bone[af]
    elif is_key == True:
        # キーONの場合、有効なのを返す
        for af in after_framenos:
            if frames_by_bone[af].key == True:
                return frames_by_bone[af]
    elif is_exist == True:
        # 既存指定の場合、自身のフレーム（指定フレームの直後のフレーム）
        return copy.deepcopy(frames_by_bone[after_framenos[0]])

    # 前フレーム
    prev_framenos = [x for x in sorted(frames_by_bone.keys()) if x < fill_bf.frame]
    prev_bf = None

    # 指定されたフレーム直前の有効キー(数が多いのからチェック)
    for p in reversed(prev_framenos):
        if frames_by_bone[p].key == True:
            prev_bf = frames_by_bone[p]
            break
    if not prev_bf:
        # 有効な前キーが取れない場合、暫定的に現在フレームの値を保持する
        prev_bf = copy.deepcopy(fill_bf)

    # 計算対象フレーム
    calc_bf = None

    # 次フレーム
    next_next_framenos = [x for x in sorted(frames_by_bone.keys()) if x > fill_bf.frame]
    next_bf = None

    # 指定されたフレーム直後のキー
    for p in next_next_framenos:
        next_bf = frames_by_bone[p]
        break
    
    if next_bf:
        # 次がある場合、次を採用
        calc_bf = copy.deepcopy(next_bf)
    else:
        if len(now_framenos) > 0:
            # 現在がある場合、現在キー
            calc_bf = copy.deepcopy(frames_by_bone[now_framenos[0]])
        else:
            # 現在も次もない場合、過去を計算対象とする
            calc_bf = copy.deepcopy(prev_bf)

        calc_bf.frame = frameno
    
    # 補間曲線を元に間を埋める
    fill_bf.rotation = calc_bone_by_complement_rot(prev_bf, calc_bf, fill_bf)
    fill_bf.position = calc_bone_by_complement_pos(prev_bf, calc_bf, fill_bf)
    
    return fill_bf

def calc_bone_by_complement_rot(prev_bf, calc_bf, fill_bf):
    if prev_bf.rotation != calc_bf.rotation:
        # 回転補間曲線
        _, _, rn = utils.calc_interpolate_bezier(calc_bf.complement[utils.R_x1_idxs[3]], calc_bf.complement[utils.R_y1_idxs[3]], \
            calc_bf.complement[utils.R_x2_idxs[3]], calc_bf.complement[utils.R_y2_idxs[3]], prev_bf.frame, calc_bf.frame, fill_bf.frame)
        return QQuaternion.slerp(prev_bf.rotation, calc_bf.rotation, rn)

    return copy.deepcopy(prev_bf.rotation)

def calc_bone_by_complement_pos(prev_bf, calc_bf, fill_bf):

    # 補間曲線を元に間を埋める
    if prev_bf.position != calc_bf.position:
        # http://rantyen.blog.fc2.com/blog-entry-65.html
        # X移動補間曲線
        _, _, xn = utils.calc_interpolate_bezier(calc_bf.complement[0], calc_bf.complement[4], calc_bf.complement[8], calc_bf.complement[12], prev_bf.frame, calc_bf.frame, fill_bf.frame)
        # Y移動補間曲線
        _, _, yn = utils.calc_interpolate_bezier(calc_bf.complement[16], calc_bf.complement[20], calc_bf.complement[24], calc_bf.complement[28], prev_bf.frame, calc_bf.frame, fill_bf.frame)
        # Z移動補間曲線
        _, _, zn = utils.calc_interpolate_bezier(calc_bf.complement[32], calc_bf.complement[36], calc_bf.complement[40], calc_bf.complement[44], prev_bf.frame, calc_bf.frame, fill_bf.frame)

        fill_pos = QVector3D()
        fill_pos.setX(prev_bf.position.x() + (( calc_bf.position.x() - prev_bf.position.x()) * xn))
        fill_pos.setY(prev_bf.position.y() + (( calc_bf.position.y() - prev_bf.position.y()) * yn))
        fill_pos.setZ(prev_bf.position.z() + (( calc_bf.position.z() - prev_bf.position.z()) * zn))
        
        return fill_pos
    
    return copy.deepcopy(prev_bf.position)
     
# -----------------------------

def smooth_filter(all_frames_by_bone, frames_by_bone, model, bone_name, config):
    if model.bones[bone_name].fixed_axis != QVector3D():
        # 軸制限ありの場合、フィルタをかけない
        return

    # 移動用フィルタ
    pxfilter = OneEuroFilter(**config)
    pyfilter = OneEuroFilter(**config)
    pzfilter = OneEuroFilter(**config)

    # 回転用フィルタ
    rxfilter = OneEuroFilter(**config)
    ryfilter = OneEuroFilter(**config)
    rzfilter = OneEuroFilter(**config)

    # 変化量の辞書
    filterd_rot_frames_by_bone = {}

    for e, fno in enumerate(sorted(frames_by_bone.keys())):
        now_bf = frames_by_bone[fno]
        if not now_bf: break

        # 移動XYZそれぞれにフィルターをかける
        px = pxfilter(now_bf.position.x(), now_bf.frame)
        py = pyfilter(now_bf.position.y(), now_bf.frame)
        pz = pzfilter(now_bf.position.z(), now_bf.frame)
        now_bf.position = QVector3D(px, py, pz)

        # 回転XYZそれぞれにフィルターをかける(オイラー角)
        now_qq = now_bf.rotation

        r = now_qq.toEulerAngles()
        rx = rxfilter(r.x(), now_bf.frame)
        ry = ryfilter(r.y(), now_bf.frame)
        rz = rzfilter(r.z(), now_bf.frame)

        # クォータニオンに戻して保持
        new_qq = QQuaternion.fromEulerAngles(rx, ry, rz)

        now_bf.rotation = new_qq

        # 変化量を保持
        filterd_rot_frames_by_bone[fno] = now_qq.inverted() * new_qq

    for cbone in model.bones.values():
        if cbone.parent_index == model.bones[bone_name].index:
            if cbone.fixed_axis != QVector3D():
                # # 処理対象ボーンを親にもつ子が捩りである場合、捩り再分配
                # for (e, (fno, filter_rot)) in enumerate(filterd_rot_frames_by_bone.items()):
                #     if cbone.name in all_frames_by_bone and fno in all_frames_by_bone[cbone.name]:
                #         # 該当フレームのbfが登録されている場合
                #         # 回転を分散する
                #         parent_result_qq, child_result_qq = utils.spread_qq(fno, frames_by_bone[fno].rotation, \
                #              all_frames_by_bone[cbone.name][fno].rotation, utils.BORN_ROTATION_LIMIT[(bone_name, cbone.name)], cbone.fixed_axis)

                #         frames_by_bone[fno].rotation = parent_result_qq
                #         all_frames_by_bone[cbone.name][fno].rotation = child_result_qq
                pass
            else:
                # 親の変化量を子でキャンセルする
                for (e, (fno, filter_rot)) in enumerate(filterd_rot_frames_by_bone.items()):
                    if cbone.name in all_frames_by_bone and fno in all_frames_by_bone[cbone.name]:
                        # 該当フレームのbfが登録されている場合
                        all_frames_by_bone[cbone.name][fno].rotation = filter_rot.inverted() * all_frames_by_bone[cbone.name][fno].rotation


# OneEuroFilter
# オリジナル：https://www.cristal.univ-lille.fr/~casiez/1euro/
# ----------------------------------------------------------------------------

class LowPassFilter(object):

    def __init__(self, alpha):
        self.__setAlpha(alpha)
        self.__y = self.__s = None

    def __setAlpha(self, alpha):
        alpha = float(alpha)
        if alpha<=0 or alpha>1.0:
            raise ValueError("alpha (%s) should be in (0.0, 1.0]"%alpha)
        self.__alpha = alpha

    def __call__(self, value, timestamp=None, alpha=None):        
        if alpha is not None:
            self.__setAlpha(alpha)
        if self.__y is None:
            s = value
        else:
            s = self.__alpha*value + (1.0-self.__alpha)*self.__s
        self.__y = value
        self.__s = s
        return s

    def lastValue(self):
        return self.__y
    
    # IK用処理スキップ
    def skip(self, value):
        self.__y = value
        self.__s = value

        return value

# ----------------------------------------------------------------------------

class OneEuroFilter(object):

    def __init__(self, freq, mincutoff=1.0, beta=0.0, dcutoff=1.0):
        if freq<=0:
            raise ValueError("freq should be >0")
        if mincutoff<=0:
            raise ValueError("mincutoff should be >0")
        if dcutoff<=0:
            raise ValueError("dcutoff should be >0")
        self.__freq = float(freq)
        self.__mincutoff = float(mincutoff)
        self.__beta = float(beta)
        self.__dcutoff = float(dcutoff)
        self.__x = LowPassFilter(self.__alpha(self.__mincutoff))
        self.__dx = LowPassFilter(self.__alpha(self.__dcutoff))
        self.__lasttime = None
        
    def __alpha(self, cutoff):
        te    = 1.0 / self.__freq
        tau   = 1.0 / (2*math.pi*cutoff)
        return  1.0 / (1.0 + tau/te)

    def __call__(self, x, timestamp=None):
        # ---- update the sampling frequency based on timestamps
        if self.__lasttime and timestamp:
            self.__freq = 1.0 / (timestamp-self.__lasttime)
        self.__lasttime = timestamp
        # ---- estimate the current variation per second
        prev_x = self.__x.lastValue()
        dx = 0.0 if prev_x is None else (x-prev_x)*self.__freq # FIXME: 0.0 or value?
        edx = self.__dx(dx, timestamp, alpha=self.__alpha(self.__dcutoff))
        # ---- use it to update the cutoff frequency
        cutoff = self.__mincutoff + self.__beta*math.fabs(edx)
        # ---- filter the given value
        return self.__x(x, timestamp, alpha=self.__alpha(cutoff))

    # IK用処理スキップ
    def skip(self, x, timestamp=None):
        # ---- update the sampling frequency based on timestamps
        if self.__lasttime and timestamp and self.__lasttime != timestamp:
            self.__freq = 1.0 / (timestamp-self.__lasttime)
        self.__lasttime = timestamp
        prev_x = self.__x.lastValue()
        self.__dx.skip(prev_x)
        self.__x.skip(x)

if __name__=="__main__":
    # Parse arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--vmd_path', dest='vmd_path', help='input vmd', type=str)
    parser.add_argument('--pmx_path', dest='pmx_path', help='pmx_path vmd', type=str)
    parser.add_argument('--smooth_cnt', dest='smooth_cnt', help='smooth_cnt', type=int)
    parser.add_argument('--is_comp_circle', dest='is_comp_circle', help='is_comp_circle', type=int)
    parser.add_argument('--is_seam_smooth', dest='is_seam_smooth', help='is_seam_smooth', type=int)

    args = parser.parse_args()

    if wrapperutils.is_valid_file(args.vmd_path, "VMDファイル", ".vmd", True) == False:
        sys.exit(-1)

    if wrapperutils.is_valid_file(args.pmx_path, "PMXファイル", ".pmx", True) == False:
        sys.exit(-1)

    main(args.vmd_path, args.pmx_path, args.smooth_cnt, args.is_comp_circle == 1, args.is_seam_smooth == 1)

    # # VMD読み込み
    # motion = VmdReader().read_vmd_file(args.vmd_path)
    # smoothed_frames = []

    # # PMX読み込み
    # model = PmxReader().read_pmx_file(args.pmx_path)

    # if len(motion.frames.values()) > 0:
    #     smooth_vmd_fpath = re.sub(r'\.vmd$', "_smooth_{0:%Y%m%d_%H%M%S}.vmd".format(datetime.now()), args.vmd_path)
        
    #     # まずボーン回転の分散
    #     sub_arm_stance.spread_rotation(motion, copy.deepcopy(motion.frames), model)

    #     for k, bf_list in motion.frames.items():
    #         for bf in bf_list:
    #             if bf.key == True:
    #                 smoothed_frames.append(bf)

    # morph_frames = []
    # for k,v in motion.morphs.items():
    #     for mf in v:
    #         morph_frames.append(mf)

    # writer = VmdWriter()
    
    # # ボーンモーション生成
    # writer.write_vmd_file(smooth_vmd_fpath, "Smooth Vmd", smoothed_frames, morph_frames, [], [], [], motion.showiks)
