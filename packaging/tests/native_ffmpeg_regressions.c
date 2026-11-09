/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Component-level regressions, linked against real old/fixed libavutil. */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "libavutil/avstring.h"
#include "libavutil/encryption_info.h"
#include "libavutil/hdr_dynamic_metadata.h"
#include "libavutil/mem.h"
#include "libavutil/intreadwrite.h"
#include "libavformat/avformat.h"

int main(int argc, char **argv)
{
    if (argc < 2) return 100;
    if (!strcmp(argv[1], "rtsp")) {
        AVFormatContext *context = NULL;
        if (argc != 3) return 104;
        av_log_set_level(AV_LOG_ERROR);
        avformat_network_init();
        int result = avformat_open_input(&context, argv[2], NULL, NULL);
        avformat_close_input(&context);
        avformat_network_deinit();
        return result < 0 ? 0 : 105;
    }
    if (!strcmp(argv[1], "pssh")) {
        AVFormatContext *context = NULL;
        int found = 0;
        if (argc != 4) return 106;
        av_log_set_level(AV_LOG_ERROR);
        int result = avformat_open_input(&context, argv[2], NULL, NULL);
        if (result >= 0 && context->nb_streams) {
            AVCodecParameters *parameters = context->streams[0]->codecpar;
            for (int i = 0; i < parameters->nb_coded_side_data; i++) {
                AVPacketSideData *data = &parameters->coded_side_data[i];
                if (data->type == AV_PKT_DATA_ENCRYPTION_INIT_INFO &&
                    data->size >= 4 && AV_RB32(data->data) == atoi(argv[3])) found = 1;
            }
        }
        avformat_close_input(&context);
        return found ? 0 : 107;
    }
    if (!strcmp(argv[1], "empty-replace")) {
        char *text = av_strireplace("abc", "", "replacement");
        int result = !text || strcmp(text, "abc");
        av_free(text);
        return result;
    }
    if (!strcmp(argv[1], "deep-free")) {
        AVEncryptionInitInfo *first = NULL;
        for (int i = 0; i < 70000; i++) {
            AVEncryptionInitInfo *node = av_encryption_init_info_alloc(0, 0, 0, 0);
            if (!node) return 101;
            node->next = first;
            first = node;
        }
        av_encryption_init_info_free(first);
        return 0;
    }
    if (!strcmp(argv[1], "hdr-initialized")) {
        AVDynamicHDRPlus input = {0};
        uint8_t a[1024], b[1024], *pa = a, *pb = b;
        size_t na = sizeof(a), nb = sizeof(b);
        input.num_windows = 1;
        input.targeted_system_display_maximum_luminance.den = 1;
        for (int i = 0; i < 3; i++) input.params[0].maxscl[i].den = 1;
        input.params[0].average_maxrgb.den = 1;
        input.params[0].fraction_bright_pixels.den = 1;
        input.params[0].color_saturation_mapping_flag = 1;
        input.params[0].color_saturation_weight.num = 1;
        input.params[0].color_saturation_weight.den = 1;
        memset(a, 0xAA, sizeof(a));
        memset(b, 0x55, sizeof(b));
        if (av_dynamic_hdr_plus_to_t35(&input, &pa, &na) < 0 ||
            av_dynamic_hdr_plus_to_t35(&input, &pb, &nb) < 0) return 102;
        printf("payload sizes: %zu, %zu\n", na, nb);
        return na != nb || memcmp(a, b, na) != 0;
    }
    return 103;
}

